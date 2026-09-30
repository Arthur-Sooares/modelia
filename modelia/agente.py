"""Orquestrador do Modelia: os modos Explicar, Demonstrar e Verificar (RF4 a RF7).

A LLM nunca fala direto com o Blender. Quando ela pede para executar código, o agente
guarda o pedido como "pendente" e devolve o controle à interface, que mostra o código
com os botões Executar e Cancelar (RF6, OWASP LLM06 - agência excessiva).

Estados de um passo do Demonstrar:
    aguardando pedido -> LLM pensando -> aguardando confirmação -> executando
        -> (erro e ainda há tentativas) LLM pensando
        -> (sucesso ou cancelado) aguardando "próximo"
"""

import json
import time
import unicodedata
from dataclasses import dataclass, field

from modelia import config, llm
from modelia.blender import FERRAMENTA_EXECUTAR, FERRAMENTAS_LEITURA
from modelia.tarefas import TAREFAS

EXPLICAR, DEMONSTRAR, VERIFICAR = "Explicar", "Demonstrar", "Verificar"
MODOS = [EXPLICAR, DEMONSTRAR, VERIFICAR]

NAO_SEI = "NAO_SEI"  # a LLM responde isto quando o material não cobre a dúvida

BASE = (
    "Você é o Modelia, um tutor de modelagem 3D no Blender 5.1 para estudantes iniciantes. "
    "Responda sempre em português do Brasil, com frases curtas e palavras simples."
)

SISTEMA_EXPLICAR = BASE + (
    " Responda à dúvida usando SOMENTE os trechos do material que vêm na mensagem. "
    "Não invente e não use o que você sabe de fora do material. "
    f"Se os trechos não bastarem para responder, responda apenas {NAO_SEI}."
)

SISTEMA_DEMONSTRAR = BASE + (
    " Você demonstra uma tarefa no Blender UM PASSO POR VEZ. Em cada passo:\n"
    "1. Explique em até 3 frases o que vai fazer e qual atalho de teclado o aluno usaria "
    "para fazer o mesmo à mão (por exemplo Shift+A para adicionar, G mover, R girar, S escalar, Tab modo de edição).\n"
    "2. Chame a ferramenta execute_blender_code com o código Python (bpy) SÓ desse passo.\n"
    "Se precisar saber o que já existe na cena, use as ferramentas de leitura antes. "
    "Para devolver dados do código, atribua um dicionário à variável result. "
    "Depois de cada passo, espere o aluno pedir o próximo. "
    "Quando a tarefa estiver completa, não chame ferramentas: responda com um resumo curto do que foi feito "
    "e a lista dos atalhos usados."
)

SISTEMA_VERIFICAR = BASE + (
    " O aluno fez uma tarefa sozinho no Blender e quer saber se está certa. Você recebe a meta, a medição "
    "da cena e a correção automática. Dê um feedback encorajador em três partes: o que está certo, o que falta "
    "e UMA dica do próximo passo. Não dê a solução completa nem código. Você pode usar as ferramentas de "
    "leitura para ver detalhes de um objeto, mas nunca altera a cena."
)

PALAVRAS_PROXIMO = {"proximo", "proxima", "continuar", "continue", "segue", "sim", "ok", "pode", "vai"}
PALAVRAS_PARAR = {"parar", "pare", "chega", "encerrar", "sair"}


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.lower().strip(" .!?")


@dataclass
class Resposta:
    """O que a interface deve mostrar depois de cada ação."""
    texto: str
    codigo: str = ""               # preenchido quando há código esperando confirmação
    atualizar_cena: bool = False   # a interface deve pedir uma nova captura da cena


@dataclass
class Estado:
    """Estado de uma conversa. Existe um por aba do navegador (gr.State)."""
    sessao_id: int
    modelo: str
    modo: str = EXPLICAR
    mensagens: list = field(default_factory=list)
    pendente: dict | None = None     # {"codigo", "interacao"} esperando o clique
    tentativas: int = 0              # erros seguidos no passo atual
    em_demonstracao: bool = False
    interacao: dict | None = None    # interação em andamento: {"id", "inicio", "resposta", "erros"}


class Agente:
    def __init__(self, blender, conhecimento, banco, conversar=llm.conversar):
        self.blender = blender
        self.conhecimento = conhecimento
        self.banco = banco
        self.conversar = conversar

    # --- registro (RNF10) ---

    def _abrir_interacao(self, estado, pedido):
        interacao_id = self.banco.registrar_interacao(estado.sessao_id, estado.modo, estado.modelo, pedido)
        estado.interacao = {"id": interacao_id, "inicio": time.perf_counter(), "resposta": [], "erros": 0}

    def _anotar(self, estado, texto, fontes="", tarefa=""):
        """Acrescenta texto à resposta da interação atual e grava na hora."""
        atual = estado.interacao
        if not atual:
            return
        atual["resposta"].append(texto)
        self.banco.concluir_interacao(
            atual["id"], "\n\n".join(atual["resposta"]), time.perf_counter() - atual["inicio"],
            erros=atual["erros"], fontes=fontes, tarefa=tarefa)

    # --- entrada principal ---

    def responder(self, estado, mensagem, tarefa_id=""):
        mensagem = (mensagem or "").strip()
        if not mensagem:
            return Resposta("Escreva uma mensagem.")
        if estado.pendente:
            return Resposta("Antes de continuar, clique em **Executar** ou **Cancelar** no código proposto.",
                            codigo=estado.pendente["codigo"])
        self._abrir_interacao(estado, mensagem)
        if estado.modo == EXPLICAR:
            return self._explicar(estado, mensagem)
        if estado.modo == VERIFICAR:
            return self._verificar(estado, mensagem, tarefa_id)
        return self._demonstrar(estado, mensagem)

    def trocar_modo(self, estado, modo):
        """Mudar de modo começa um contexto novo para a LLM e encerra a demonstração em andamento."""
        estado.modo = modo
        estado.mensagens = []
        estado.pendente = None
        estado.tentativas = 0
        estado.em_demonstracao = False

    # --- RF4: Explicar ---

    def _explicar(self, estado, pergunta):
        try:
            trechos = self.conhecimento.buscar(pergunta)
        except Exception as erro:
            texto = f"Não consegui consultar a base de conhecimento ({erro})."
            self._anotar(estado, texto)
            return Resposta(texto)
        if not trechos:
            return self._nao_coberto(estado)

        contexto = "\n\n".join(f"[{i}] ({t['titulo']})\n{t['texto']}" for i, t in enumerate(trechos, 1))
        estado.mensagens = [
            {"role": "system", "content": SISTEMA_EXPLICAR},
            {"role": "user", "content": f"Trechos do material:\n{contexto}\n\nDúvida do aluno: {pergunta}"},
        ]
        # Neste modo a LLM não recebe nenhuma ferramenta (RF4).
        resposta = self.conversar(estado.modelo, estado.mensagens).message.content or ""
        if NAO_SEI in resposta:
            return self._nao_coberto(estado)
        fontes = sorted({f"{t['titulo']}" + (f" ({t['fonte']})" if t["fonte"] else "") for t in trechos})
        texto = resposta.strip() + "\n\n**Fontes:** " + "; ".join(fontes)
        self._anotar(estado, texto, fontes="; ".join(fontes))
        return Resposta(texto)

    def _nao_coberto(self, estado):
        texto = ("O material da disciplina não cobre esse assunto. Tente reformular a pergunta "
                 "com outras palavras, por exemplo usando o nome da ferramenta do Blender.")
        self._anotar(estado, texto)
        return Resposta(texto)

    # --- RF7: Verificar ---

    def _verificar(self, estado, pergunta, tarefa_id):
        tarefa = TAREFAS.get(tarefa_id)
        if not tarefa:
            texto = "Escolha a tarefa-meta antes de pedir a verificação."
            self._anotar(estado, texto)
            return Resposta(texto)
        try:
            cena = self.blender.medir_cena()
        except Exception as erro:
            texto = str(erro)
            self._anotar(estado, texto, tarefa=tarefa.id)
            return Resposta(texto)
        if not cena["malhas"]:
            texto = ("Sua cena ainda não tem nenhum objeto de malha. Comece a tarefa "
                     f"**{tarefa.nome}** no Blender, ou use o modo **Demonstrar** para ver como se faz.")
            self._anotar(estado, texto, tarefa=tarefa.id)
            return Resposta(texto, atualizar_cena=True)

        nota, certo, falta = tarefa.verificar(cena)
        estado.mensagens = [
            {"role": "system", "content": SISTEMA_VERIFICAR},
            {"role": "user", "content": (
                f"Meta da tarefa: {tarefa.pedido}\n\n"
                f"Medição da cena (metros): {json.dumps(cena, ensure_ascii=False)}\n\n"
                f"Correção automática: nota {nota}/3.\nCerto: {certo}\nFalta: {falta}\n\n"
                f"Pergunta do aluno: {pergunta}")},
        ]
        # Só ferramentas de leitura (RF7): o trabalho do aluno não pode ser alterado.
        texto = self._laco_leitura(estado, self.blender.ferramentas(FERRAMENTAS_LEITURA))
        self._anotar(estado, texto, tarefa=tarefa.id)
        return Resposta(texto, atualizar_cena=True)

    def _laco_leitura(self, estado, ferramentas):
        """Deixa a LLM usar ferramentas de leitura até ela responder com texto."""
        for _ in range(config.MAX_RODADAS):
            mensagem = self.conversar(estado.modelo, estado.mensagens, ferramentas).message
            estado.mensagens.append(mensagem)
            if not mensagem.tool_calls:
                return (mensagem.content or "").strip()
            for chamada in mensagem.tool_calls:
                self._responder_ferramenta_leitura(estado, chamada)
        return "Não consegui terminar a análise. Tente perguntar de novo."

    def _responder_ferramenta_leitura(self, estado, chamada):
        nome = chamada.function.name
        if nome in FERRAMENTAS_LEITURA:
            saida, _ = self.blender.chamar(nome, dict(chamada.function.arguments))
        else:
            saida = f"A ferramenta '{nome}' não está disponível neste modo."
        estado.mensagens.append({"role": "tool", "content": saida[:4000], "tool_name": nome})

    # --- RF5 e RF6: Demonstrar ---

    def _demonstrar(self, estado, mensagem):
        comando = normalizar(mensagem)
        if estado.em_demonstracao and comando in PALAVRAS_PARAR:
            estado.em_demonstracao = False
            estado.mensagens = []
            texto = "Demonstração encerrada. A cena ficou como estava."
            self._anotar(estado, texto)
            return Resposta(texto)

        if not self.blender.disponivel():
            texto = ("O Blender não está conectado. Abra o Blender 5.1 e ligue o add-on MCP "
                     f"(porta {config.PORTA_BLENDER}).")
            self._anotar(estado, texto)
            return Resposta(texto)

        if not estado.em_demonstracao:
            estado.mensagens = [{"role": "system", "content": SISTEMA_DEMONSTRAR},
                                {"role": "user", "content": mensagem}]
            estado.em_demonstracao = True
        elif comando in PALAVRAS_PROXIMO:
            estado.mensagens.append({"role": "user", "content": "Pode fazer o próximo passo."})
        else:
            estado.mensagens.append({"role": "user", "content": mensagem})
        estado.tentativas = 0
        return self._proximo_passo(estado)

    def _proximo_passo(self, estado, prefixo=""):
        """Roda a LLM até ela propor código (para e espera o clique) ou encerrar a tarefa."""
        ferramentas = self.blender.ferramentas(FERRAMENTAS_LEITURA | {FERRAMENTA_EXECUTAR})
        for _ in range(config.MAX_RODADAS):
            mensagem = self.conversar(estado.modelo, estado.mensagens, ferramentas).message
            estado.mensagens.append(mensagem)
            if not mensagem.tool_calls:
                estado.em_demonstracao = False
                texto = prefixo + (mensagem.content or "").strip()
                self._anotar(estado, texto)
                return Resposta(texto + "\n\n*Demonstração concluída. Faça um novo pedido quando quiser.*")

            codigo = None
            for chamada in mensagem.tool_calls:
                nome = chamada.function.name
                if nome == FERRAMENTA_EXECUTAR and codigo is None:
                    codigo = str(chamada.function.arguments.get("code", ""))
                elif nome == FERRAMENTA_EXECUTAR:
                    estado.mensagens.append({"role": "tool", "tool_name": nome,
                                             "content": "Ignorado: faça um passo por vez."})
                else:
                    self._responder_ferramenta_leitura(estado, chamada)
            if codigo is not None:
                estado.pendente = {"codigo": codigo, "interacao": estado.interacao["id"]}
                explicacao = (mensagem.content or "").strip() or "Vou executar este passo no Blender."
                texto = prefixo + explicacao
                self._anotar(estado, texto)
                return Resposta(texto + "\n\nConfira o código abaixo e clique em **Executar** ou **Cancelar**.",
                                codigo=codigo)
        estado.em_demonstracao = False
        texto = prefixo + "A LLM não conseguiu propor um passo. Tente descrever a tarefa de outro jeito."
        self._anotar(estado, texto)
        return Resposta(texto)

    def executar(self, estado):
        """Clique em "Executar" (RF6). Só aqui o código da LLM chega ao Blender."""
        pendente, estado.pendente = estado.pendente, None
        if not pendente:
            return Resposta("Não há código esperando confirmação.")
        try:
            saida, erro = self.blender.executar(pendente["codigo"])
        except Exception as falha:
            saida, erro = str(falha), True
        self.banco.registrar_codigo(pendente["interacao"], pendente["codigo"], "executado", saida[:4000], erro)

        if erro:
            estado.tentativas += 1
            if estado.interacao:
                estado.interacao["erros"] += 1
            estado.mensagens.append({"role": "tool", "tool_name": FERRAMENTA_EXECUTAR, "content": (
                f"ERRO do Blender: {saida[:3000]}\nCorrija o código deste mesmo passo.")})
            if estado.tentativas >= config.MAX_TENTATIVAS:
                estado.em_demonstracao = False
                estado.mensagens = []
                texto = (f"O Blender recusou o código {estado.tentativas} vezes seguidas, então parei a "
                         "demonstração. Tente pedir a tarefa de outro jeito ou com passos menores.")
                self._anotar(estado, texto)
                return Resposta(texto, atualizar_cena=True)
            aviso = (f"O Blender devolveu um erro (tentativa {estado.tentativas} de {config.MAX_TENTATIVAS}):\n"
                     f"```\n{mensagem_de_erro(saida)}\n```\nPedi uma correção à LLM.\n\n")
            return self._proximo_passo(estado, prefixo=aviso)

        estado.tentativas = 0
        # O agente confere a cena em vez de confiar na própria resposta (RF5).
        try:
            cena = self.blender.medir_cena()
            conferencia = f"\nCena depois do passo: {json.dumps(cena, ensure_ascii=False)[:2500]}"
        except Exception:
            conferencia = ""
        estado.mensagens.append({"role": "tool", "tool_name": FERRAMENTA_EXECUTAR,
                                 "content": f"Executado com sucesso: {saida[:1500]}{conferencia}"})
        texto = "Passo executado. Veja a cena ao lado. Escreva **próximo** para continuar ou **parar** para encerrar."
        self._anotar(estado, "[executado]")
        return Resposta(texto, atualizar_cena=True)

    def cancelar(self, estado):
        """Clique em "Cancelar" (RF6): nada é executado e a LLM fica sabendo."""
        pendente, estado.pendente = estado.pendente, None
        if not pendente:
            return Resposta("Não há código esperando confirmação.")
        self.banco.registrar_codigo(pendente["interacao"], pendente["codigo"], "cancelado")
        estado.mensagens.append({"role": "tool", "tool_name": FERRAMENTA_EXECUTAR,
                                 "content": "O aluno cancelou. O código NÃO foi executado e a cena não mudou."})
        self._anotar(estado, "[cancelado]")
        return Resposta("Cancelado: nada foi executado. Escreva **próximo** para a LLM propor outro "
                        "caminho, dê uma instrução diferente ou escreva **parar**.")


def mensagem_de_erro(saida):
    """Tira a mensagem legível do JSON de erro do Blender."""
    try:
        dados = json.loads(saida)
        return str(dados.get("message") or dados)[:800]
    except (ValueError, AttributeError):
        return saida[:800]
