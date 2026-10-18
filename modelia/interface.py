"""Interface web do Modelia em Gradio (RF1 a RF10, RNF1, RNF5, RNF6).

Duas telas: a identificação (RF1) e a principal, com as abas. O Estudante vê só o Chat;
o Pesquisador vê também Modelos, Conhecimento, Histórico e Experimento. A restrição é
conferida também no servidor, e não só escondendo as abas.
"""

import tempfile
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import gradio as gr
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from modelia import config, experimento, llm
from modelia.agente import DEMONSTRAR, ENSINAR, EXPLICAR, MODOS, VERIFICAR, Agente, Estado
from modelia.api import criar_api
from modelia.banco import Banco, para_csv
from modelia.blender import Blender
from modelia.conhecimento import Conhecimento
from modelia.tarefas import TAREFAS

AJUDA_MODOS = {
    EXPLICAR: "Tire dúvidas sobre o Blender. A resposta vem do material da disciplina e não mexe na cena.",
    ENSINAR: "Peça uma tarefa (ex.: \"me ensine a fazer uma mesa\"). O Modelia faz um passo por vez no "
             "Blender, explica o atalho e só executa depois do seu clique em Executar.",
    DEMONSTRAR: "Peça um objeto (ex.: \"crie uma mesa\"). O Modelia cria tudo sozinho no Blender, confere "
                "a cena e corrige o que ficou fora do lugar.",
    VERIFICAR: "Faça a tarefa sozinho no Blender, escolha a tarefa-meta e pergunte \"minha cena está certa?\". "
               "Nada na sua cena é alterado.",
}
PASTA_SITE = Path(__file__).parent / "site"
ESCOLHAS_TAREFAS = [(t.nome, t.id) for t in TAREFAS.values()]
COLUNAS_HISTORICO = ["data", "estudante", "modo", "modelo", "pedido", "resposta", "tarefa", "fontes",
                     "codigo_executado", "erros", "segundos"]


class Servicos:
    """Objetos compartilhados por todas as abas abertas do navegador."""

    def __init__(self):
        self.banco = Banco()
        self.blender = Blender()
        self.conhecimento = Conhecimento()
        self.agente = Agente(self.blender, self.conhecimento, self.banco)
        self.parar_experimento = threading.Event()


def exigir_pesquisador(perfil):
    if perfil != "pesquisador":
        raise gr.Error("Esta função é só para o Pesquisador.")


def nomes_de_modelos():
    try:
        return llm.modelos_de_chat()
    except Exception:
        return []


def criar_app(servicos=None):
    s = servicos or Servicos()

    # --- funções da tela de identificação e do estado do sistema (RF1, RF2) ---

    def situacao():
        partes = []
        if llm.disponivel():
            partes.append("**Ollama:** conectado")
        else:
            partes.append("**Ollama:** desligado. Abra o aplicativo Ollama (ou rode `ollama serve`).")
        if s.blender.disponivel():
            partes.append("**Blender:** conectado")
        else:
            partes.append(f"**Blender:** desconectado. Abra o Blender 5.1 e ligue o add-on MCP "
                          f"(porta {config.PORTA_BLENDER}). Explicar funciona sem ele.")
        if s.conhecimento.vazia():
            partes.append("**Base de conhecimento:** vazia. O Pesquisador precisa adicionar o material "
                          "na aba Conhecimento para o modo Explicar funcionar.")
        return "  \n".join(partes)

    def comecar(identificador, eh_pesquisador, senha):
        perfil = "estudante"
        if eh_pesquisador:
            if senha != config.SENHA_PESQUISADOR:
                raise gr.Error("Senha do Pesquisador incorreta.")
            perfil = "pesquisador"
        try:
            sessao_id = s.banco.iniciar_sessao(identificador)
        except ValueError as erro:
            raise gr.Error(str(erro)) from erro
        modelos = nomes_de_modelos()
        modelo = config.MODELO_PADRAO if config.MODELO_PADRAO in modelos else (modelos[0] if modelos else "")
        estado = Estado(sessao_id=sessao_id, modelo=modelo)
        restrito = perfil == "pesquisador"
        return (
            estado, perfil,
            gr.Column(visible=False), gr.Column(visible=True),
            f"Participante **{identificador.strip()}** · perfil {perfil}",
            situacao(),
            gr.Dropdown(choices=modelos, value=modelo or None),
            s.blender.captura() if s.blender.disponivel() else None,
            *[gr.Tab(visible=restrito)] * 4,
        )

    # --- chat (RF2, RF4 a RF7) ---

    def mostrar(resposta, historico):
        historico = historico + [{"role": "assistant", "content": resposta.texto}]
        pendente = bool(resposta.codigo)
        return (
            historico,
            gr.Code(value=resposta.codigo, visible=pendente),
            gr.Row(visible=pendente),
            s.blender.captura() if resposta.atualizar_cena else gr.skip(),
        )

    def enviar(mensagem, historico, estado, tarefa):
        """Saídas: mensagem, chat, código, botões, cena. Mostra a pergunta antes de a LLM responder."""
        if not estado:
            raise gr.Error("Identifique-se primeiro.")
        if not estado.modelo:
            raise gr.Error("Nenhum modelo de LLM instalado. Peça ao Pesquisador para baixar um na aba Modelos.")
        mensagem = (mensagem or "").strip()
        if not mensagem:
            yield gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()
            return
        historico = historico + [{"role": "user", "content": mensagem}]
        yield "", historico, gr.skip(), gr.skip(), gr.skip()
        try:
            resposta = s.agente.responder(estado, mensagem, tarefa_id=tarefa or "")
        except Exception as erro:
            raise gr.Error(f"Algo deu errado: {erro}") from erro
        yield "", *mostrar(resposta, historico)

    def executar(historico, estado):
        historico = historico + [{"role": "user", "content": "[Executar]"}]
        return mostrar(s.agente.executar(estado), historico)

    def cancelar(historico, estado):
        historico = historico + [{"role": "user", "content": "[Cancelar]"}]
        return mostrar(s.agente.cancelar(estado), historico)

    def trocar_modo(modo, estado):
        if estado:
            s.agente.trocar_modo(estado, modo)
        return (gr.Dropdown(visible=modo == VERIFICAR), AJUDA_MODOS[modo],
                gr.Code(value="", visible=False), gr.Row(visible=False))

    def trocar_modelo(modelo, estado):
        if estado and modelo:
            estado.modelo = modelo

    # --- aba Modelos (RF3) ---

    def tabela_modelos(perfil):
        exigir_pesquisador(perfil)
        linhas = [[m["nome"], m["tamanho_gb"], m["modificado"],
                   "sim" if llm.cabe_na_vram(m["tamanho_gb"]) else "não (vai usar a RAM e ficar lento)"]
                  for m in llm.modelos()]
        nomes = [linha[0] for linha in linhas]
        return linhas, gr.Dropdown(choices=nomes, value=None), gr.Dropdown(choices=nomes_de_modelos())

    def baixar_modelo(nome, perfil, progresso=gr.Progress()):
        exigir_pesquisador(perfil)
        if not (nome or "").strip():
            raise gr.Error("Digite o nome do modelo, por exemplo qwen3.5:4b.")
        try:
            for fracao, status in llm.baixar(nome):
                progresso(fracao, desc=status)
        except Exception as erro:
            raise gr.Error(f"Não consegui baixar {nome}: {erro}") from erro
        tamanho = next((m["tamanho_gb"] for m in llm.modelos() if m["nome"].startswith(nome.strip())), 0)
        aviso = "" if llm.cabe_na_vram(tamanho) else (
            f" Atenção: {tamanho} GB não cabe nos {config.VRAM_GB:g} GB de VRAM; o modelo vai rodar devagar.")
        return f"{nome} baixado.{aviso}", *tabela_modelos(perfil)

    def apagar_modelo(nome, confirmado, perfil):
        exigir_pesquisador(perfil)
        if not nome:
            raise gr.Error("Escolha o modelo a apagar.")
        if not confirmado:
            raise gr.Error("Marque a caixa de confirmação antes de apagar.")
        llm.apagar(nome)
        return f"{nome} apagado.", *tabela_modelos(perfil), gr.Checkbox(value=False)

    # --- aba Conhecimento (RF8) ---

    def tabela_conhecimento(perfil):
        exigir_pesquisador(perfil)
        documentos = s.conhecimento.listar()
        linhas = [[d["titulo"], d["fonte"], d["trechos"]] for d in documentos]
        escolhas = [(d["titulo"], d["doc_id"]) for d in documentos]
        return linhas, gr.Dropdown(choices=escolhas, value=None)

    def adicionar_documento(arquivo, titulo, fonte, perfil, progresso=gr.Progress()):
        exigir_pesquisador(perfil)
        if not arquivo:
            raise gr.Error("Escolha um arquivo PDF, Markdown ou texto.")
        progresso(None, desc="Dividindo o texto e gerando os embeddings...")
        try:
            trechos = s.conhecimento.adicionar(arquivo, titulo, fonte)
        except Exception as erro:
            if "not found" in str(erro).lower():
                raise gr.Error(f"Instale o modelo de embeddings na aba Modelos: {config.MODELO_EMBEDDINGS}") from erro
            raise gr.Error(f"Não consegui indexar: {erro}") from erro
        return f"Documento incluído com {trechos} trechos.", *tabela_conhecimento(perfil), None

    def remover_documento(doc_id, confirmado, perfil):
        exigir_pesquisador(perfil)
        if not doc_id:
            raise gr.Error("Escolha o documento a remover.")
        if not confirmado:
            raise gr.Error("Marque a caixa de confirmação antes de remover.")
        s.conhecimento.remover(doc_id)
        return "Documento removido.", *tabela_conhecimento(perfil), gr.Checkbox(value=False)

    # --- aba Histórico (RF9) ---

    def filtrar_historico(estudante, modo, desde, ate, perfil):
        exigir_pesquisador(perfil)
        for data in (desde, ate):
            if data:
                try:
                    datetime.strptime(data, "%Y-%m-%d")
                except ValueError as erro:
                    raise gr.Error("Use datas no formato AAAA-MM-DD, por exemplo 2026-10-15.") from erro
        linhas = s.banco.historico(estudante or "", modo or "", desde or "", ate or "")
        if not linhas:
            return "Não há registros para os filtros escolhidos.", [], gr.Dropdown(choices=s.banco.estudantes())
        por_modo = {m: sum(1 for linha in linhas if linha["modo"] == m) for m in MODOS}
        erros = sum(linha["erros"] for linha in linhas)
        resumo = (f"**{len(linhas)} interações** · " + " · ".join(f"{m}: {n}" for m, n in por_modo.items())
                  + f" · erros do Blender: {erros}")
        return (resumo, [[linha[c] for c in COLUNAS_HISTORICO] for linha in linhas],
                gr.Dropdown(choices=[""] + s.banco.estudantes()))

    def exportar_historico(estudante, modo, desde, ate, perfil):
        exigir_pesquisador(perfil)
        linhas = s.banco.historico(estudante or "", modo or "", desde or "", ate or "")
        if not linhas:
            raise gr.Error("Não há registros para exportar.")
        caminho = Path(tempfile.gettempdir()) / f"modelia_historico_{datetime.now():%Y%m%d_%H%M%S}.csv"
        caminho.write_text(para_csv(linhas), encoding="utf-8-sig")  # -sig: o Excel abre os acentos certos
        return gr.File(value=str(caminho), visible=True)

    # --- aba Experimento (RF10) ---

    def total_previsto(modelos, tarefas, repeticoes, condicoes):
        total = len(modelos or []) * len(tarefas or []) * int(repeticoes or 0) * len(condicoes or [])
        return f"Execuções previstas: **{total}**"

    def criar_cena_base(perfil):
        exigir_pesquisador(perfil)
        try:
            experimento.criar_cena_base(s.blender)
        except Exception as erro:
            raise gr.Error(str(erro)) from erro
        return f"Cena base salva em `{config.CENA_BASE}`."

    def iniciar_experimento(modelos, tarefas, condicoes, repeticoes, temperatura, seeds_texto, perfil):
        exigir_pesquisador(perfil)
        if not modelos or not tarefas or not condicoes:
            raise gr.Error("Escolha pelo menos um modelo, uma tarefa e uma condição.")
        try:
            seeds = [int(x) for x in (seeds_texto or "1").replace(" ", "").split(",") if x]
        except ValueError as erro:
            raise gr.Error("As seeds devem ser números separados por vírgula, por exemplo 1,2,3.") from erro
        s.parar_experimento.clear()
        linhas, arquivo = [], None
        try:
            for feitas, total, linha, caminho_csv in experimento.rodar(
                    s.blender, modelos, tarefas, int(repeticoes), float(temperatura), seeds, condicoes,
                    deve_parar=s.parar_experimento.is_set):
                arquivo = caminho_csv
                linhas.insert(0, [linha.get(c, "") for c in experimento.CAMPOS[:13]])
                yield f"Execução {feitas} de {total} concluída.", linhas, gr.File(visible=False)
        except FileNotFoundError as erro:
            raise gr.Error(f"{erro} Use o botão \"Criar cena base\".") from erro
        fim = "Experimento interrompido." if s.parar_experimento.is_set() else "Experimento concluído."
        yield (f"{fim} Resultados em `{arquivo}`.", linhas,
               gr.File(value=str(arquivo), visible=True) if arquivo else gr.File(visible=False))

    def parar_experimento(perfil):
        exigir_pesquisador(perfil)
        s.parar_experimento.set()
        return "Parando: a execução atual termina e o que já foi gravado fica salvo."

    # --- montagem da tela ---

    with gr.Blocks(title="Modelia") as app:
        estado = gr.State(None)
        perfil = gr.State("estudante")

        gr.Markdown("# Modelia\nTutor de modelagem 3D no Blender")

        with gr.Column(visible=True) as tela_entrada:
            identificador = gr.Textbox(label="Código de participante", placeholder="Ex.: P07",
                                       info="Use o código que o professor deu. Não digite seu nome completo.")
            eh_pesquisador = gr.Checkbox(label="Sou o Pesquisador",
                                         info="Libera as abas de modelos, conhecimento, histórico e experimento.")
            senha = gr.Textbox(label="Senha do Pesquisador", type="password", visible=False,
                               info="Definida na variável de ambiente MODELIA_SENHA.")
            botao_comecar = gr.Button("Começar", variant="primary")

        with gr.Column(visible=False) as tela_principal:
            cabecalho = gr.Markdown()
            with gr.Tabs():
                with gr.Tab("Chat"):
                    with gr.Row():
                        situacao_md = gr.Markdown()
                        botao_situacao = gr.Button("Verificar conexões", size="sm", scale=0)
                    with gr.Row():
                        with gr.Column(scale=3):
                            with gr.Row():
                                modelo = gr.Dropdown(label="Modelo de LLM", choices=[],
                                                     info="A LLM que vai responder. Roda neste computador.")
                                modo = gr.Radio(MODOS, value=EXPLICAR, label="Modo",
                                                info="Explicar tira dúvidas, Ensinar faz passo a passo, "
                                                     "Demonstrar cria sozinho, Verificar corrige a sua cena.")
                            ajuda_modo = gr.Markdown(AJUDA_MODOS[EXPLICAR])
                            tarefa = gr.Dropdown(ESCOLHAS_TAREFAS, label="Tarefa-meta", visible=False,
                                                 info="A tarefa que você fez e quer que o Modelia confira.")
                            chat = gr.Chatbot(label="Conversa", height=460)
                            codigo = gr.Code(label="Código proposto (ainda NÃO executado)", language="python",
                                             interactive=False, visible=False)
                            with gr.Row(visible=False) as botoes_codigo:
                                botao_executar = gr.Button("Executar", variant="primary")
                                botao_cancelar = gr.Button("Cancelar", variant="stop")
                            with gr.Row():
                                mensagem = gr.Textbox(label="Mensagem", scale=5, submit_btn=False,
                                                      placeholder="Escreva aqui e aperte Enter",
                                                      info="Em Ensinar, escreva \"próximo\" para o passo "
                                                           "seguinte ou \"parar\" para encerrar.")
                                botao_enviar = gr.Button("Enviar", variant="primary", scale=1)
                        with gr.Column(scale=2):
                            cena = gr.Image(label="Cena do Blender", type="pil", interactive=False)
                            botao_cena = gr.Button("Atualizar imagem da cena", size="sm")

                with gr.Tab("Modelos", visible=False) as aba_modelos:
                    gr.Markdown("Modelos de LLM instalados no Ollama deste computador.")
                    tabela_mod = gr.Dataframe(headers=["Modelo", "Tamanho (GB)", "Modificado em",
                                                       f"Cabe em {config.VRAM_GB:g} GB de VRAM?"],
                                              interactive=False)
                    with gr.Row():
                        nome_baixar = gr.Textbox(label="Baixar modelo", placeholder="Ex.: qwen3.5:4b",
                                                 info="Nome (tag) do modelo no ollama.com/library.")
                        botao_baixar = gr.Button("Baixar", variant="primary", scale=0)
                    with gr.Row():
                        modelo_apagar = gr.Dropdown(label="Apagar modelo", choices=[],
                                                    info="Libera espaço em disco.")
                        confirmar_apagar = gr.Checkbox(label="Confirmo que quero apagar")
                        botao_apagar = gr.Button("Apagar", variant="stop", scale=0)
                    aviso_mod = gr.Markdown()

                with gr.Tab("Conhecimento", visible=False) as aba_conhecimento:
                    gr.Markdown("Material usado pelo modo Explicar (apostila, trechos do manual do Blender).")
                    with gr.Row():
                        arquivo = gr.File(label="Arquivo", file_types=[".pdf", ".md", ".txt"], type="filepath")
                        with gr.Column():
                            titulo = gr.Textbox(label="Título", info="Aparece como fonte nas respostas.")
                            fonte = gr.Textbox(label="Fonte", info="Autor, site ou edição do material.")
                            botao_adicionar = gr.Button("Adicionar", variant="primary")
                    tabela_con = gr.Dataframe(headers=["Título", "Fonte", "Trechos"], interactive=False)
                    with gr.Row():
                        doc_remover = gr.Dropdown(label="Remover documento", choices=[])
                        confirmar_remover = gr.Checkbox(label="Confirmo que quero remover")
                        botao_remover = gr.Button("Remover", variant="stop", scale=0)
                    aviso_con = gr.Markdown()

                with gr.Tab("Histórico", visible=False) as aba_historico:
                    with gr.Row():
                        filtro_estudante = gr.Dropdown(label="Estudante", choices=[""], value="",
                                                       allow_custom_value=True, info="Vazio = todos.")
                        filtro_modo = gr.Dropdown(label="Modo", choices=[""] + MODOS, value="",
                                                  info="Vazio = todos.")
                        filtro_desde = gr.Textbox(label="Desde", placeholder="AAAA-MM-DD",
                                                  info="Primeiro dia, inclusive.")
                        filtro_ate = gr.Textbox(label="Até", placeholder="AAAA-MM-DD",
                                                info="Último dia, inclusive.")
                    with gr.Row():
                        botao_filtrar = gr.Button("Filtrar", variant="primary")
                        botao_exportar = gr.Button("Exportar CSV")
                    resumo_hist = gr.Markdown()
                    tabela_hist = gr.Dataframe(headers=COLUNAS_HISTORICO, interactive=False, wrap=True)
                    arquivo_hist = gr.File(label="CSV do histórico", visible=False)

                with gr.Tab("Experimento", visible=False) as aba_experimento:
                    gr.Markdown("Bateria de tarefas para comparar as LLMs. Cada execução parte da cena base "
                                "limpa. Ao clicar em **Iniciar** você autoriza a execução automática do "
                                "código gerado, só durante o experimento.")
                    with gr.Row():
                        exp_modelos = gr.CheckboxGroup(label="Modelos", choices=nomes_de_modelos(),
                                                       info="Os modelos que vão ser comparados.")
                        exp_tarefas = gr.CheckboxGroup(label="Tarefas", choices=ESCOLHAS_TAREFAS,
                                                       info="Cada tarefa tem correção automática de 0 a 3.")
                        exp_condicoes = gr.CheckboxGroup(label="Condições", choices=experimento.CONDICOES,
                                                         value=experimento.CONDICOES,
                                                         info="puro: só o modelo. modelia: o modo Demonstrar.")
                    with gr.Row():
                        exp_repeticoes = gr.Number(label="Repetições", value=3, minimum=1, precision=0,
                                                   info="Quantas vezes cada modelo faz cada tarefa.")
                        exp_temperatura = gr.Slider(0, 1.5, value=0.6, step=0.1, label="Temperatura",
                                                    info="Quanto maior, mais variadas as respostas.")
                        exp_seeds = gr.Textbox(label="Seeds", value="1,2,3",
                                               info="Uma por repetição, separadas por vírgula.")
                    previsto = gr.Markdown(total_previsto([], [], 3, experimento.CONDICOES))
                    with gr.Row():
                        botao_cena_base = gr.Button("Criar cena base")
                        botao_iniciar = gr.Button("Iniciar", variant="primary")
                        botao_parar = gr.Button("Parar", variant="stop")
                    progresso_exp = gr.Markdown()
                    tabela_exp = gr.Dataframe(headers=experimento.CAMPOS[:13], interactive=False)
                    arquivo_exp = gr.File(label="CSV do experimento", visible=False)

        # --- ligações ---

        eh_pesquisador.change(lambda marcado: gr.Textbox(visible=marcado), eh_pesquisador, senha)
        entrada = [identificador, eh_pesquisador, senha]
        saida_entrada = [estado, perfil, tela_entrada, tela_principal, cabecalho, situacao_md, modelo, cena,
                         aba_modelos, aba_conhecimento, aba_historico, aba_experimento]
        botao_comecar.click(comecar, entrada, saida_entrada)
        identificador.submit(comecar, entrada, saida_entrada)

        botao_situacao.click(situacao, None, situacao_md)
        botao_cena.click(lambda: s.blender.captura(), None, cena)
        modo.change(trocar_modo, [modo, estado], [tarefa, ajuda_modo, codigo, botoes_codigo])
        modelo.change(trocar_modelo, [modelo, estado], None)

        saida_chat = [mensagem, chat, codigo, botoes_codigo, cena]
        botao_enviar.click(enviar, [mensagem, chat, estado, tarefa], saida_chat)
        mensagem.submit(enviar, [mensagem, chat, estado, tarefa], saida_chat)
        botao_executar.click(executar, [chat, estado], [chat, codigo, botoes_codigo, cena])
        botao_cancelar.click(cancelar, [chat, estado], [chat, codigo, botoes_codigo, cena])

        aba_modelos.select(tabela_modelos, perfil, [tabela_mod, modelo_apagar, modelo])
        botao_baixar.click(baixar_modelo, [nome_baixar, perfil], [aviso_mod, tabela_mod, modelo_apagar, modelo])
        botao_apagar.click(apagar_modelo, [modelo_apagar, confirmar_apagar, perfil],
                           [aviso_mod, tabela_mod, modelo_apagar, modelo, confirmar_apagar])

        aba_conhecimento.select(tabela_conhecimento, perfil, [tabela_con, doc_remover])
        botao_adicionar.click(adicionar_documento, [arquivo, titulo, fonte, perfil],
                              [aviso_con, tabela_con, doc_remover, arquivo])
        botao_remover.click(remover_documento, [doc_remover, confirmar_remover, perfil],
                            [aviso_con, tabela_con, doc_remover, confirmar_remover])

        filtros = [filtro_estudante, filtro_modo, filtro_desde, filtro_ate, perfil]
        aba_historico.select(filtrar_historico, filtros, [resumo_hist, tabela_hist, filtro_estudante])
        botao_filtrar.click(filtrar_historico, filtros, [resumo_hist, tabela_hist, filtro_estudante])
        botao_exportar.click(exportar_historico, filtros, arquivo_hist)

        aba_experimento.select(lambda: gr.CheckboxGroup(choices=nomes_de_modelos()), None, exp_modelos)
        for campo in (exp_modelos, exp_tarefas, exp_repeticoes, exp_condicoes):
            campo.change(total_previsto, [exp_modelos, exp_tarefas, exp_repeticoes, exp_condicoes], previsto)
        botao_cena_base.click(criar_cena_base, perfil, progresso_exp)
        botao_iniciar.click(iniciar_experimento,
                            [exp_modelos, exp_tarefas, exp_condicoes, exp_repeticoes, exp_temperatura, exp_seeds,
                             perfil],
                            [progresso_exp, tabela_exp, arquivo_exp])
        botao_parar.click(parar_experimento, perfil, progresso_exp)

    return app


def criar_servidor():
    """A home e o chat (site/) em "/", a API do chat em "/api" e o app em Gradio em "/app"."""
    servicos = Servicos()
    servidor = FastAPI()
    servidor.include_router(criar_api(servicos))
    gr.mount_gradio_app(servidor, criar_app(servicos), path="/app", footer_links=[],
                        allowed_paths=[str(config.PASTA_RESULTADOS)])
    servidor.mount("/", StaticFiles(directory=PASTA_SITE, html=True), name="site")  # por último: pega o resto
    return servidor


def iniciar():
    # RNF1 e RNF2: só no endereço local, sem link público.
    endereco = f"http://{config.HOST}:{config.PORTA}/"
    threading.Timer(1.5, webbrowser.open, [endereco]).start()
    threading.Thread(target=llm.aquecer, args=(config.MODELO_PADRAO,), daemon=True).start()
    uvicorn.run(criar_servidor(), host=config.HOST, port=config.PORTA)
