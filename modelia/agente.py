"""Orquestrador do Modelia: os modos Explicar, Ensinar, Demonstrar e Verificar (RF4 a RF7).

No Ensinar a LLM nunca fala direto com o Blender. Quando ela pede para executar código, o agente
guarda o pedido como "pendente" e devolve o controle à interface, que mostra o código
com os botões Executar e Cancelar (RF6, OWASP LLM06 - agência excessiva).
No Demonstrar o Modelia cria o objeto sozinho: executa, mede a cena e pede correção à LLM
até nada ficar flutuando ou abaixo do chão. O add-on do Blender continua bloqueando
operações perigosas.

Estados de um passo do Ensinar:
    aguardando pedido -> LLM pensando -> aguardando confirmação -> executando
        -> (erro e ainda há tentativas) LLM pensando
        -> (sucesso ou cancelado) aguardando "próximo"
"""

import ast
import functools
import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field

from modelia import config, llm, pecas, receitas
from modelia.blender import AJUDANTES, FERRAMENTA_EXECUTAR, FERRAMENTAS_LEITURA
from modelia.tarefas import TAREFAS

EXPLICAR, ENSINAR, DEMONSTRAR, VERIFICAR = "Explicar", "Ensinar", "Demonstrar", "Verificar"
MODOS = [EXPLICAR, ENSINAR, DEMONSTRAR, VERIFICAR]

NAO_SEI = "NAO_SEI"  # a LLM responde isto quando o material não cobre a dúvida

BASE = (
    "Você é o Modelia, um tutor de modelagem 3D no Blender 5.1 para estudantes iniciantes. "
    "Responda sempre em português do Brasil, com frases curtas e palavras simples."
)

# Medido em 18/10/2026 com o qwen3.5:4b: "aumentar e diminuir objeto" passou a achar "Escala",
# "arredondar as quinas" achou "Chanfro", "copio um objeto" achou "Duplicar" (sem a reescrita, erravam).
SISTEMA_BUSCA = (
    "Você transforma a dúvida de um aluno iniciante em uma busca no manual do Blender.\n"
    "Pense em qual ferramenta ou comando do Blender faz o que o aluno descreveu. Corrija os erros de digitação "
    "e escreva o nome oficial da ferramenta em português e em inglês, com o atalho de teclado se houver.\n"
    "Exemplos:\n"
    "\"como puxo uma face pra fora\" -> Extrusão (Extrude), atalho E, modo de edição\n"
    "\"como giro o objeto de lado\" -> Rotação (Rotate) de objeto, atalho R\n"
    "\"como faço um buraco num cubo usando outro\" -> Modificador Booleano (Boolean), diferença\n"
    "Responda só com a busca, numa linha, sem explicar. Se a dúvida não for sobre o Blender, responda só FORA.")

# Medido em 18/10/2026 com o qwen3.5:4b: 14 de 15 frases certas; o erro ("como entrar no mode edicao"
# saiu TAREFA) cai no lado seguro, que é o Ensinar de antes.
SISTEMA_TIPO = (
    "O aluno escreveu no modo Ensinar do Modelia, que ensina fazendo: o Modelia executa no Blender, passo a passo, "
    "uma tarefa de modelagem.\n"
    "TAREFA: a mensagem cita um objeto ou resultado para construir ou mudar na cena (mesa, cubo, luz, cadeira, "
    "\"deixar o cubo maior\"), mesmo em forma de pergunta. Ex.: \"como faço uma estante?\" -> TAREFA\n"
    "DUVIDA: a mensagem quer entender uma ferramenta, comando, modo ou atalho, sem um objeto para construir. "
    "Ex.: \"como funciona a ferramenta de girar?\" -> DUVIDA; \"como eu uso o modo escultura?\" -> DUVIDA\n"
    "Responda só uma palavra: TAREFA ou DUVIDA.")

SISTEMA_EXPLICAR = BASE + (
    " Responda à dúvida usando SOMENTE os trechos do material que vêm na mensagem. "
    "Não invente e não use o que você sabe de fora do material. "
    f"Se os trechos não bastarem para responder, responda apenas {NAO_SEI}.\n"
    "Os exemplos abaixo só mostram o formato da resposta. Os trechos deles NÃO fazem parte do material.\n"
    "Exemplo 1 (os trechos respondem):\n"
    "Trechos do material:\n"
    "[1] (Modo de Edição)\n"
    "A extrusão puxa a face selecionada e cria geometria nova ligada à malha. No modo de edição (Tab), "
    "selecione a face, aperte E, mova o mouse e clique para confirmar.\n"
    "Dúvida do aluno: como eu puxo uma face para fora?\n"
    "Resposta: Use a extrusão. Ela puxa a face e cria geometria nova ligada à malha. "
    "Entre no modo de edição com Tab, selecione a face e aperte E. Mova o mouse e clique para confirmar.\n"
    "Exemplo 2 (os trechos não respondem):\n"
    "Trechos do material: os mesmos do exemplo 1.\n"
    "Dúvida do aluno: como eu renderizo uma animação?\n"
    f"Resposta: {NAO_SEI}"
)

SISTEMA_ENSINAR = BASE + (
    " Você ensina uma tarefa no Blender UM PASSO POR VEZ. Em cada passo:\n"
    "1. Explique em até 3 frases o que vai fazer e qual atalho de teclado o aluno usaria "
    "para fazer o mesmo à mão (por exemplo Shift+A para adicionar, G mover, R girar, S escalar, Tab modo de edição).\n"
    "2. Chame a ferramenta execute_blender_code com o código Python (bpy) SÓ desse passo e a explicação "
    "no campo explicacao.\n"
    "O chão fica em z = 0: objetos que ficam apoiados devem encostar nele.\n"
    "Se precisar saber o que já existe na cena, use as ferramentas de leitura antes. "
    "Para devolver dados do código, atribua um dicionário à variável result. "
    "Depois de cada passo, espere o aluno pedir o próximo. "
    "Quando a tarefa estiver completa, não chame ferramentas: responda com um resumo curto do que foi feito "
    "e a lista dos atalhos usados.\n"
    "O exemplo abaixo só mostra o formato. Para outra tarefa, crie os passos DESSA tarefa.\n"
    "Exemplo, pedido \"faça um cone de trânsito\":\n"
    "Passo 1, explicacao: Vou criar a base: uma placa de 0,4 m x 0,4 m com 0,04 m de altura, apoiada no chão. "
    "À mão: Shift+A, Malha, Cubo; depois S para escalar e G, Z para subir.\n"
    "Passo 1, code:\n"
    "import bpy\n"
    "bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.02))\n"
    "bpy.context.object.name = 'Base'\n"
    "bpy.context.object.scale = (0.4, 0.4, 0.04)\n"
    "Passo 2, explicacao: Agora o cone, com 0,6 m de altura, em cima da base. O topo da base está em "
    "z = 0,04, então o centro do cone fica em 0,04 + 0,3 = 0,34. À mão: Shift+A, Malha, Cone; depois G, Z.\n"
    "Passo 2, code:\n"
    "import bpy\n"
    "bpy.ops.mesh.primitive_cone_add(radius1=0.15, radius2=0.02, depth=0.6, location=(0, 0, 0.34))\n"
    "bpy.context.object.name = 'Cone'\n"
    "Resumo final: Pronto! O cone de trânsito tem uma base no chão e um cone apoiado nela. "
    "Atalhos usados: Shift+A (adicionar), S (escalar), G e Z (mover na altura)."
)

SISTEMA_DEMONSTRAR = BASE + (
    " Agora você cria no Blender, sozinho e de uma vez, o objeto que o aluno pediu. "
    "Chame a ferramenta execute_blender_code UMA vez, com o código Python completo do objeto inteiro.\n"
    "O código já começa com estas funções prontas. Monte o objeto só com elas, sem bpy.ops, "
    "e NÃO escreva def caixa, def cilindro, def cone, def esfera, def limpar_cena nem def ao_lado (elas já existem):\n"
    "- caixa(nome, largura, profundidade, altura, x=0, y=0, base=0, cor='#B8B8B8', chanfro=0.01, giro=0)\n"
    "- cilindro(nome, raio, altura, x=0, y=0, base=0, cor='#B8B8B8', chanfro=0.005, raio_topo=None, eixo='z'): "
    "raio_topo diferente de raio faz um tronco de cone (cúpula, vaso, pé torneado); eixo='x' ou 'y' deita o "
    "cilindro (rolo, rodinha, barra), com o comprimento altura centrado em x e y\n"
    "- cone(nome, raio, altura, x=0, y=0, base=0, cor='#B8B8B8', raio_topo=0)\n"
    "- esfera(nome, raio, x=0, y=0, base=0, cor='#B8B8B8', achatar=1.0): achatar abaixo de 1 deixa mais baixa "
    "(almofada, pão, cúpula); a altura fica 2 * raio * achatar\n"
    "- cilindro_oco(nome, raio, altura, x=0, y=0, base=0, cor='#B8B8B8', raio_topo=None): aberto em cima e "
    "com fundo (copo, vaso, caneca, balde)\n"
    "- alca(nome, raio, grossura, x=0, y=0, base=0, cor='#B8B8B8', giro=0): meio anel em pé que sai para +X "
    "a partir de x; encoste x na parede da peça (alça de caneca)\n"
    "- limpar_cena(): apaga todas as malhas.\n"
    "- with ao_lado(dx, dy=0): as peças criadas dentro do bloco saem deslocadas dx em X e dy em Y.\n"
    "Medidas em metros. largura vai no eixo X, profundidade no Y, altura no Z. x e y são o centro da peça. "
    "base é a altura (z) do ponto mais BAIXO da peça; o chão é z = 0. giro gira a caixa em graus em volta do eixo Z.\n"
    "Método para QUALQUER objeto:\n"
    "1. Pense em como o objeto real é e divida em peças simples (caixas, cilindros, cones, esferas). "
    "Inclua os detalhes que fazem ele ser reconhecido (puxadores, rodapé, braços, alça, tampa).\n"
    "2. No começo do código, escreva um comentário com a lista das peças e as medidas reais de cada uma.\n"
    "3. Monte de baixo para cima: uma peça apoiada em outra tem base = base + altura da peça de baixo. "
    "Peças lado a lado se encostam: o centro fica a meia largura de cada uma. Nada pode flutuar.\n"
    "Pedido com vários objetos (mesa com cadeira): um objeto não pode ficar dentro do outro. "
    "Crie o primeiro normalmente e TODAS as peças do segundo dentro de \"with ao_lado(1.2):\", "
    "com as mesmas medidas que teriam sozinho; não some o deslocamento à mão.\n"
    "4. Dê nomes claros às peças e cores que combinem (madeira #8B5A2B ou #A0703C, metal #3A3A3A, "
    "tecido #5B6C8F, branco #EDEDE8, cerâmica #D9D4CC).\n"
    "Medidas típicas (metros): mesa 1,4 x 0,8 com topo a 0,75; cadeira com assento 0,45 x 0,45 a 0,45 do chão "
    "e encosto até 0,9; banco alto 0,75; cama de solteiro 1,9 x 0,9, colchão de 0,2 sobre estrado a 0,25; "
    "sofá 2,0 x 0,85, assento a 0,42, encosto até 0,8, braços até 0,6; estante 0,8 x 0,3 x 1,8 com prateleiras "
    "a cada 0,35; armário 1,0 x 0,55 x 2,0; criado-mudo 0,45 x 0,4 x 0,55; luminária de mesa 0,45 de altura; "
    "caneca raio 0,04 e altura 0,1; garrafa raio 0,035 e altura 0,3; porta 0,8 x 0,04 x 2,1.\n"
    "Os exemplos abaixo só mostram o método. Para outro objeto, crie as peças DESSE objeto.\n"
    "Exemplo 1, mesa (pernas recuadas dos cantos, saia logo abaixo do tampo):\n"
    "madeira = '#8B5A2B'\n"
    "caixa('Tampo', 1.4, 0.8, 0.04, base=0.71, cor=madeira)\n"
    "for i, (x, y) in enumerate([(-0.62, -0.32), (0.62, -0.32), (-0.62, 0.32), (0.62, 0.32)]):\n"
    "    caixa(f'Perna{i + 1}', 0.06, 0.06, 0.71, x=x, y=y, cor=madeira)\n"
    "caixa('SaiaFrente', 1.18, 0.02, 0.08, y=-0.32, base=0.63, cor=madeira)\n"
    "caixa('SaiaTras', 1.18, 0.02, 0.08, y=0.32, base=0.63, cor=madeira)\n"
    "Exemplo 2, cadeira (4 pernas sob o assento; o encosto é uma placa fina em pé no topo do assento, "
    "na borda de trás, e não toca o chão):\n"
    "madeira = '#A0703C'\n"
    "for i, (x, y) in enumerate([(-0.19, -0.19), (0.19, -0.19), (-0.19, 0.19), (0.19, 0.19)]):\n"
    "    caixa(f'Perna{i + 1}', 0.04, 0.04, 0.43, x=x, y=y, cor=madeira)\n"
    "caixa('Assento', 0.45, 0.45, 0.04, base=0.43, cor=madeira)\n"
    "caixa('Encosto', 0.45, 0.03, 0.43, y=0.21, base=0.47, cor=madeira)\n"
    "Exemplo 3, luminária de mesa (a haste entra um pouco na cúpula):\n"
    "metal, cupula = '#3A3A3A', '#EDEDE8'\n"
    "cilindro('Base', 0.08, 0.02, cor=metal)\n"
    "cilindro('Haste', 0.01, 0.33, base=0.02, cor=metal)\n"
    "cilindro('Cupula', 0.12, 0.15, base=0.3, raio_topo=0.06, cor=cupula)\n"
    "Se o pedido trouxer medidas, use as medidas do pedido. "
    "Depois de executar, responda em UMA frase curta o que foi criado. "
    "Se o Modelia apontar um problema, mande o código completo do objeto de novo, já corrigido."
)

SISTEMA_VERIFICAR = BASE + (
    " O aluno fez uma tarefa sozinho no Blender e quer saber se está certa. Você recebe a meta, a medição "
    "da cena e a correção automática. Dê um feedback encorajador em três partes: o que está certo, o que falta "
    "e UMA dica do próximo passo. Não dê a solução completa nem código. Você pode usar as ferramentas de "
    "leitura para ver detalhes de um objeto, mas nunca altera a cena.\n"
    "O exemplo abaixo só mostra o formato. Use sempre a meta e a correção que vêm na mensagem.\n"
    "Exemplo:\n"
    "Meta da tarefa: Crie um cilindro de 1 m de altura apoiado no chão.\n"
    "Correção automática: nota 2/3.\n"
    "Certo: [\"Cilindro 'Cylinder' com 1 m de altura.\"]\n"
    "Falta: ['A base do cilindro está em z = -0.5; ela deve ficar no chão (z = 0).']\n"
    "Pergunta do aluno: está certo?\n"
    "Resposta:\n"
    "**O que está certo:** você criou o cilindro com 1 m de altura. Muito bem!\n"
    "**O que falta:** ele está metade enterrado. A base está em z = -0,5 e deveria estar no chão (z = 0).\n"
    "**Dica:** selecione o cilindro e mova no eixo Z (G, depois Z). Quanto ele precisa subir para a base "
    "encostar no chão?"
)

PALAVRAS_PROXIMO = {"proximo", "proxima", "continuar", "continue", "segue", "sim", "ok", "pode", "vai"}
PALAVRAS_PARAR = {"parar", "pare", "chega", "encerrar", "sair"}


def normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.lower().strip(" .!?")


BLENDER_FECHADO = "blender_fechado"


@dataclass
class Resposta:
    """O que a interface deve mostrar depois de cada ação."""
    texto: str
    codigo: str = ""               # preenchido quando há código esperando confirmação
    atualizar_cena: bool = False   # a interface deve pedir uma nova captura da cena
    detalhes: dict | None = None   # Verificar: {"tarefa", "nota", "certo", "falta"} para o cartão de resultado
    problema: str = ""             # "blender_fechado": o chat mostra o passo a passo para abrir o Blender


@dataclass
class Estado:
    """Estado de uma conversa. Existe um por aba do navegador (gr.State)."""
    sessao_id: int
    modelo: str
    modo: str = EXPLICAR
    mensagens: list = field(default_factory=list)
    pendente: dict | None = None     # {"codigo", "interacao"} esperando o clique
    tentativas: int = 0              # erros seguidos no passo atual
    em_ensino: bool = False
    interacao: dict | None = None    # interação em andamento: {"id", "inicio", "resposta", "erros"}


def na_fila(metodo):
    """Um pedido por vez. Dois pedidos juntos dividiriam a LLM e mexeriam na mesma cena:
    uma nova tentativa do Demonstrar apagaria os objetos que o outro pedido acabou de criar."""
    @functools.wraps(metodo)
    def na_vez(self, *args, ao_escrever=None, ao_avisar=None, **kwargs):
        # ao_escrever: recebe o texto da LLM enquanto ela escreve (streaming no chat).
        # ao_avisar: recebe a etapa atual ("Criando no Blender…") enquanto o aluno espera.
        with self._fila:
            self._ao_escrever, self._ao_avisar = ao_escrever, ao_avisar
            try:
                return metodo(self, *args, **kwargs)
            finally:
                self._ao_escrever = self._ao_avisar = None
    return na_vez


class Agente:
    def __init__(self, blender, conhecimento, banco, conversar=llm.conversar):
        self.blender = blender
        self.conhecimento = conhecimento
        self.banco = banco
        self.conversar = conversar
        # ponytail: fila única para todos os alunos; serve a um PC por aluno, como no laboratório.
        self._fila = threading.RLock()
        self._ao_escrever = self._ao_avisar = None

    def _etapa(self, texto):
        if self._ao_avisar:
            self._ao_avisar(texto)

    def _falar(self, estado, ferramentas=None):
        """Uma rodada com a LLM; com streaming quando quem pediu quer ver o texto chegando."""
        extra = {"ao_escrever": self._ao_escrever} if self._ao_escrever else {}
        return self.conversar(estado.modelo, estado.mensagens, ferramentas, **extra)

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

    @na_fila
    def responder(self, estado, mensagem, tarefa_id=""):
        mensagem = (mensagem or "").strip()
        if not mensagem:
            return Resposta("Escreva uma mensagem.")
        aviso = ""
        if estado.pendente:
            # Escrever outra coisa vale como Cancelar: o código não roda sem o clique (RF6), mas o chat não trava.
            self._cancelar_pendente(estado, "O aluno escreveu outra mensagem sem executar o código. "
                                            "Ele NÃO foi executado e a cena não mudou.")
            aviso = "*O código anterior não foi executado.*\n\n"
        self._abrir_interacao(estado, mensagem)
        if aviso:
            resposta = self._responder_no_modo(estado, mensagem, tarefa_id)
            resposta.texto = aviso + resposta.texto
            return resposta
        return self._responder_no_modo(estado, mensagem, tarefa_id)

    def _responder_no_modo(self, estado, mensagem, tarefa_id):
        if estado.modo == EXPLICAR:
            return self._explicar(estado, mensagem)
        if estado.modo == VERIFICAR:
            return self._verificar(estado, mensagem, tarefa_id)
        if estado.modo == DEMONSTRAR:
            return self._demonstrar(estado, mensagem)
        return self._ensinar(estado, mensagem)

    def trocar_modo(self, estado, modo):
        """Mudar de modo começa um contexto novo para a LLM e encerra a demonstração em andamento."""
        estado.modo = modo
        estado.mensagens = []
        estado.pendente = None
        estado.tentativas = 0
        estado.em_ensino = False

    # --- RF4: Explicar ---

    def _termos_do_blender(self, estado, pergunta):
        """Reescreve a dúvida com os nomes oficiais das ferramentas (query rewriting). O aluno diz
        "aumentar o objeto"; o manual diz "Escala". Vazio quando não ajuda: aí busca só a pergunta."""
        try:
            busca = self.conversar(estado.modelo, [{"role": "system", "content": SISTEMA_BUSCA},
                                                   {"role": "user", "content": pergunta}]).message.content or ""
        except Exception:
            return ""
        busca = busca.strip().splitlines()[0] if busca.strip() else ""
        return "" if "FORA" in busca else busca

    def _e_duvida(self, estado, mensagem):
        """No Ensinar: a mensagem é uma dúvida (DUVIDA) em vez de uma tarefa para construir? Erro = tarefa."""
        try:
            tipo = self.conversar(estado.modelo, [{"role": "system", "content": SISTEMA_TIPO},
                                                  {"role": "user", "content": mensagem}]).message.content or ""
        except Exception:
            return False
        return "DUVIDA" in tipo.upper() and "TAREFA" not in tipo.upper()

    def _explicar(self, estado, pergunta):
        self._etapa("Procurando no manual…")
        termos = self._termos_do_blender(estado, pergunta)
        try:
            trechos = self.conhecimento.buscar(f"{pergunta}\n{termos}" if termos else pergunta)
        except Exception as erro:
            texto = f"Não consegui consultar a base de conhecimento ({erro})."
            self._anotar(estado, texto)
            return Resposta(texto)
        if not trechos:
            return self._nao_coberto(estado)

        contexto = "\n\n".join(f"[{i}] ({t['titulo']})\n{t['texto']}" for i, t in enumerate(trechos, 1))
        estado.mensagens = [
            {"role": "system", "content": SISTEMA_EXPLICAR},
            {"role": "user", "content": f"Trechos do material:\n{contexto}\n\nDúvida do aluno: {pergunta}"
                                        + (f"\n(No Blender, isso se chama: {termos})" if termos else "")},
        ]
        # Neste modo a LLM não recebe nenhuma ferramenta (RF4).
        resposta = self._falar(estado).message.content or ""
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
            return Resposta(texto, problema=BLENDER_FECHADO if isinstance(erro, ConnectionError) else "")
        if not cena["malhas"]:
            texto = ("Sua cena ainda não tem nenhum objeto de malha. Comece a tarefa "
                     f"**{tarefa.nome}** no Blender, ou use **Me ensina passo a passo** para ver como se faz.")
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
        detalhes = {"tarefa": tarefa.nome, "nota": nota, "certo": certo, "falta": falta}
        return Resposta(texto, atualizar_cena=True, detalhes=detalhes)

    def _laco_leitura(self, estado, ferramentas):
        """Deixa a LLM usar ferramentas de leitura até ela responder com texto."""
        for _ in range(config.MAX_RODADAS):
            mensagem = self._falar(estado, ferramentas).message
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

    # --- Demonstrar: o Modelia cria o objeto sozinho ---

    def _demonstrar(self, estado, pedido):
        if not self.blender.disponivel():
            texto = ("O Blender não está conectado. Abra o Blender 5.1 e ligue o add-on MCP "
                     f"(porta {config.PORTA_BLENDER}).")
            self._anotar(estado, texto)
            return Resposta(texto, problema=BLENDER_FECHADO)
        objetos = receitas.identificar(pedido)
        if objetos:
            resposta = self._demonstrar_por_ficha(estado, lambda: self._fichas_das_receitas(estado, pedido, objetos))
        else:
            resposta = self._demonstrar_por_ficha(estado, *self._ficha_de_pecas(estado, pedido))
        if resposta:
            return resposta
        estado.mensagens = [
            {"role": "system", "content": SISTEMA_DEMONSTRAR},
            {"role": "user", "content": f"Pedido: {pedido}"},
        ]
        ferramentas = self.blender.ferramentas({FERRAMENTA_EXECUTAR})
        for tentativa in range(1, config.MAX_TENTATIVAS + 1):
            self._etapa("Planejando as peças…" if tentativa == 1 else
                        f"Corrigindo o objeto (tentativa {tentativa} de {config.MAX_TENTATIVAS})…")
            mensagem = self._falar(estado, ferramentas).message
            estado.mensagens.append(mensagem)
            codigo = next((str(c.function.arguments.get("code", "")) for c in mensagem.tool_calls or []
                           if c.function.name == FERRAMENTA_EXECUTAR), None)
            # O código pode vir também no texto, além da ferramenta: nunca mostra o código como resposta.
            no_texto, resumo = codigo_no_texto(mensagem.content or "")
            codigo = no_texto if codigo is None else codigo
            if codigo is None:
                if tentativa > 1:  # desistiu de corrigir
                    break
                texto = ("A LLM não mandou código para o Blender. "
                         "Tente pedir de outro jeito, por exemplo \"crie uma mesa\".")
                self._anotar(estado, texto)
                return Resposta(texto)
            if not any(f"{funcao}(" in codigo for funcao in ("caixa", "cilindro", "cone", "esfera")):
                # Sem as funções prontas os modelos erram a altura das peças e esquecem a cor.
                self.banco.registrar_codigo(estado.interacao["id"], codigo, "recusado")
                estado.mensagens.append({"role": "tool", "tool_name": FERRAMENTA_EXECUTAR, "content": (
                    "Não executado: monte o objeto com as funções caixa(), cilindro(), cone() e esfera(), "
                    "sem bpy.ops. Mande o código completo de novo.")})
                continue
            invalido = validar(sem_redefinicoes(codigo))
            if invalido:
                # Erro achado aqui não gasta uma ida ao Blender e explica exatamente o que corrigir.
                self.banco.registrar_codigo(estado.interacao["id"], codigo, "recusado", "\n".join(invalido), True)
                estado.interacao["erros"] += 1
                estado.mensagens.append({"role": "tool", "tool_name": FERRAMENTA_EXECUTAR, "content": (
                    "Não executado, o código tem erros:\n- " + "\n- ".join(invalido)
                    + "\nMande o código completo corrigido.")})
                continue
            try:
                # Cada pedido (e cada tentativa) recria o objeto inteiro numa cena vazia; sem isso
                # o objeto novo nasce por cima do anterior.
                self._etapa("Criando no Blender…")
                self.blender.remover_malhas_exceto(set())
                saida, erro = self.blender.executar_com_ajudantes(sem_redefinicoes(codigo))
            except Exception as falha:
                saida, erro = str(falha), True
            self.banco.registrar_codigo(estado.interacao["id"], codigo, "automatico", saida[:4000], erro)
            if erro:
                estado.interacao["erros"] += 1
                retorno = f"ERRO do Blender: {mensagem_de_erro(saida)}\nMande o código completo corrigido."
            else:
                self._etapa("Conferindo a cena…")
                problemas = problemas_da_cena(self.blender.medir_cena())
                if not problemas:
                    texto = resumo or "Pronto, o objeto foi criado."
                    self._anotar(estado, texto)
                    self._enquadrar()
                    return Resposta(texto + "\n\n*Veja a cena ao lado.*", atualizar_cena=True)
                retorno = ("Executado, mas a medição da cena mostrou problemas:\n- " + "\n- ".join(problemas)
                           + "\nMande o código completo do objeto de novo, corrigido.")
            estado.mensagens.append({"role": "tool", "tool_name": FERRAMENTA_EXECUTAR, "content": retorno})
        texto = (f"Fiz {config.MAX_TENTATIVAS} tentativas e o objeto ainda ficou com problemas. "
                 "Veja a cena ao lado e peça de novo, se quiser com mais detalhes.")
        self._anotar(estado, texto)
        self._enquadrar()
        return Resposta(texto, atualizar_cena=True)

    def _fichas_das_receitas(self, estado, pedido, objetos):
        """Objeto comum (estante, mesa, cadeira): uma ficha curta por receita. Devolve (código, frase)."""
        fichas = []
        for receita, quantidade in objetos:
            resposta = self.conversar(estado.modelo, receitas.mensagens_da_ficha(receita, pedido), None,
                                      formato=receita.esquema)
            fichas.append((receita, quantidade, receitas.ler_ficha(receita, resposta.message.content or "")))
        return receitas.montar(fichas)

    def _ficha_de_pecas(self, estado, pedido):
        """Objeto sem receita: a LLM lista as peças e onde cada uma fica.

        Devolve (preencher, corrigir): as duas devolvem (código, frase); corrigir(problemas) continua a
        mesma conversa, mostrando à LLM o que saiu errado na cena.
        """
        mensagens = pecas.mensagens_da_ficha(pedido)

        def preencher():
            texto = self.conversar(estado.modelo, mensagens, None, formato=pecas.ESQUEMA).message.content or ""
            mensagens.append({"role": "assistant", "content": texto})
            return pecas.montar(texto)

        def corrigir(problemas):
            mensagens.append({"role": "user", "content": (
                "Montei a sua ficha e a cena ficou com problemas:\n- " + "\n- ".join(problemas)
                + "\nMande a ficha inteira de novo, corrigida.")})
            return preencher()

        return preencher, corrigir

    def _demonstrar_por_ficha(self, estado, preencher, corrigir=None):
        """A LLM só preenche uma ficha (JSON) e o Modelia calcula as posições e monta as peças.

        Se a cena sair com problema e houver `corrigir`, a LLM recebe os problemas e manda a ficha de
        novo (uma vez; é curta). Devolve None quando a ficha não serviu; aí o pedido segue pelo
        caminho do código livre, como antes.
        """
        self._etapa("Preenchendo a ficha do objeto…")
        try:
            codigo, frase = preencher()
        except ValueError:  # inclui o JSON inválido (json.JSONDecodeError)
            return None
        for tentativa in range(2):
            try:
                self._etapa("Criando no Blender…")
                self.blender.remover_malhas_exceto(set())
                saida, erro = self.blender.executar_com_ajudantes(codigo)
            except Exception as falha:
                saida, erro = str(falha), True
            self.banco.registrar_codigo(estado.interacao["id"], codigo, "automatico", saida[:4000], erro)
            if erro:
                estado.interacao["erros"] += 1
                problemas = [f"O Blender deu erro: {mensagem_de_erro(saida)}"]
            else:
                self._etapa("Conferindo a cena…")
                problemas = problemas_da_cena(self.blender.medir_cena())
            if not problemas:
                self._anotar(estado, frase)
                self._enquadrar()
                return Resposta(frase + "\n\n*Veja a cena ao lado.*", atualizar_cena=True)
            if tentativa or not corrigir:
                return None
            self._etapa("Corrigindo a ficha…")
            try:
                codigo, frase = corrigir(problemas[:8])
            except ValueError:
                return None
        return None

    def _enquadrar(self):
        """Centraliza o objeto criado na vista do Blender (e na captura do chat). Falhar aqui não é erro."""
        try:
            self.blender.enquadrar()
        except Exception:
            pass

    # --- RF5 e RF6: Ensinar (passo a passo, com confirmação) ---

    def _ensinar(self, estado, mensagem):
        comando = normalizar(mensagem)
        if estado.em_ensino and comando in PALAVRAS_PARAR:
            estado.em_ensino = False
            estado.mensagens = []
            texto = "Tarefa encerrada. A cena ficou como estava."
            self._anotar(estado, texto)
            return Resposta(texto)

        if not estado.em_ensino and self.conhecimento is not None and self._e_duvida(estado, mensagem):
            # Dúvida no Ensinar virava um passo sem sentido; responde com o manual e mostra o modo certo.
            resposta = self._explicar(estado, mensagem)
            resposta.texto = ("*Isso parece uma dúvida, então respondi com o manual, como em **Tenho uma dúvida**. "
                              "No passo a passo, peça uma tarefa, por exemplo “me ensina a fazer uma mesa”.*\n\n"
                              + resposta.texto)
            return resposta

        if not self.blender.disponivel():
            texto = ("O Blender não está conectado. Abra o Blender 5.1 e ligue o add-on MCP "
                     f"(porta {config.PORTA_BLENDER}).")
            self._anotar(estado, texto)
            return Resposta(texto, problema=BLENDER_FECHADO)

        if not estado.em_ensino:
            estado.mensagens = [{"role": "system", "content": SISTEMA_ENSINAR},
                                {"role": "user", "content": mensagem}]
            estado.em_ensino = True
        elif comando in PALAVRAS_PROXIMO:
            estado.mensagens.append({"role": "user", "content": "Pode fazer o próximo passo."})
        else:
            estado.mensagens.append({"role": "user", "content": mensagem})
        estado.tentativas = 0
        return self._proximo_passo(estado)

    def _proximo_passo(self, estado, prefixo=""):
        """Roda a LLM até ela propor código (para e espera o clique) ou encerrar a tarefa."""
        ferramentas = com_explicacao(self.blender.ferramentas(FERRAMENTAS_LEITURA | {FERRAMENTA_EXECUTAR}))
        for _ in range(config.MAX_RODADAS):
            mensagem = self._falar(estado, ferramentas).message
            estado.mensagens.append(mensagem)
            codigo, explicacao = None, ""
            if not mensagem.tool_calls:
                # Modelos pequenos (qwen3.5:4b) às vezes escrevem o código no texto em vez de chamar a ferramenta.
                codigo, explicacao = codigo_no_texto(mensagem.content or "")
                if codigo is None:
                    estado.em_ensino = False
                    texto = prefixo + (mensagem.content or "").strip()
                    self._anotar(estado, texto)
                    return Resposta(texto + "\n\n*Tarefa concluída. Faça um novo pedido quando quiser.*")

            for chamada in mensagem.tool_calls or []:
                nome = chamada.function.name
                if nome == FERRAMENTA_EXECUTAR and codigo is None:
                    codigo = str(chamada.function.arguments.get("code", ""))
                    explicacao = str(chamada.function.arguments.get("explicacao", "")).strip()
                elif nome == FERRAMENTA_EXECUTAR:
                    estado.mensagens.append({"role": "tool", "tool_name": nome,
                                             "content": "Ignorado: faça um passo por vez."})
                else:
                    self._responder_ferramenta_leitura(estado, chamada)
            if codigo is not None:
                estado.pendente = {"codigo": codigo, "interacao": estado.interacao["id"]}
                explicacao = (explicacao or (mensagem.tool_calls and codigo_no_texto(mensagem.content or "")[1])
                              or "Vou executar este passo no Blender.")
                texto = prefixo + explicacao
                self._anotar(estado, texto)
                return Resposta(texto + "\n\nConfira o código abaixo e clique em **Executar** ou **Cancelar**.",
                                codigo=codigo)
        estado.em_ensino = False
        texto = prefixo + "A LLM não conseguiu propor um passo. Tente descrever a tarefa de outro jeito."
        self._anotar(estado, texto)
        return Resposta(texto)

    @na_fila
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
                estado.em_ensino = False
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

    @na_fila
    def cancelar(self, estado):
        """Clique em "Cancelar" (RF6): nada é executado e a LLM fica sabendo."""
        if not estado.pendente:
            return Resposta("Não há código esperando confirmação.")
        self._cancelar_pendente(estado, "O aluno cancelou. O código NÃO foi executado e a cena não mudou.")
        return Resposta("Cancelado: nada foi executado. Escreva **próximo** para a LLM propor outro "
                        "caminho, dê uma instrução diferente ou escreva **parar**.")

    def _cancelar_pendente(self, estado, aviso_para_llm):
        """Descarta o código que esperava o clique, registra no banco e avisa a LLM."""
        pendente, estado.pendente = estado.pendente, None
        self.banco.registrar_codigo(pendente["interacao"], pendente["codigo"], "cancelado")
        estado.mensagens.append({"role": "tool", "tool_name": FERRAMENTA_EXECUTAR, "content": aviso_para_llm})
        self._anotar(estado, "[cancelado]")


def com_explicacao(ferramentas):
    """Acrescenta à ferramenta de executar um campo obrigatório `explicacao` (RF5).

    Sem isso, modelos pequenos costumam mandar só o código, sem explicar o passo nem o atalho.
    O campo fica só entre o agente e a LLM: ao Blender vai apenas o `code`.
    """
    resultado = []
    for ferramenta in ferramentas:
        if ferramenta["function"]["name"] == FERRAMENTA_EXECUTAR:
            funcao = dict(ferramenta["function"])
            parametros = dict(funcao.get("parameters") or {})
            parametros["properties"] = {
                "explicacao": {"type": "string", "description": (
                    "Para o aluno, em português e em até 3 frases: o que este passo faz e o atalho de "
                    "teclado para fazer o mesmo à mão no Blender.")},
                **parametros.get("properties", {}),
            }
            parametros["required"] = ["explicacao", *parametros.get("required", [])]
            funcao["parameters"] = parametros
            ferramenta = {**ferramenta, "function": funcao}
        resultado.append(ferramenta)
    return resultado


FUNCOES_PRONTAS = {"caixa", "cilindro", "cone", "esfera", "cilindro_oco", "alca", "piramide", "telhado",
                   "limpar_cena", "ao_lado"}


def sem_redefinicoes(codigo):
    """Tira do código as versões que a LLM escreveu das funções prontas (ela insiste em reescrevê-las
    com bpy.ops, sem cor nem chanfro). Código que não compila volta igual: o erro aparece no Blender."""
    try:
        arvore = ast.parse(codigo)
    except SyntaxError:
        return codigo
    corpo = [n for n in arvore.body if not (isinstance(n, ast.FunctionDef) and n.name in FUNCOES_PRONTAS)]
    if len(corpo) == len(arvore.body):
        return codigo
    arvore.body = corpo
    return ast.unparse(arvore)


def _assinaturas():
    """Parâmetros de cada função pronta, lidos do próprio código delas (blender.AJUDANTES)."""
    assinaturas = {}
    for n in ast.parse(AJUDANTES).body:
        if isinstance(n, ast.FunctionDef) and n.name in FUNCOES_PRONTAS:
            nomes = [a.arg for a in n.args.args]
            assinaturas[n.name] = (nomes, nomes[:len(nomes) - len(n.args.defaults)])  # (todos, obrigatórios)
    return assinaturas


ASSINATURAS = _assinaturas()


def validar(codigo):
    """Confere o código do Demonstrar antes de ir ao Blender: sintaxe e chamadas às funções prontas
    (parâmetro que não existe, faltando ou sobrando). Devolve a lista de problemas; vazia = pode executar."""
    try:
        arvore = ast.parse(codigo)
    except SyntaxError as erro:
        return [f"Erro de sintaxe na linha {erro.lineno}: {erro.msg}."]
    problemas = []
    for n in ast.walk(arvore):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ASSINATURAS):
            continue
        if any(isinstance(a, ast.Starred) for a in n.args) or any(k.arg is None for k in n.keywords):
            continue  # *args / **kwargs: só o Blender sabe
        nomes, obrigatorios = ASSINATURAS[n.func.id]
        chamada = f"Linha {n.lineno}, {n.func.id}()"
        if len(n.args) > len(nomes):
            problemas.append(f"{chamada}: recebeu {len(n.args)} valores sem nome, mas aceita no máximo {len(nomes)}.")
        estranhos = [k.arg for k in n.keywords if k.arg not in nomes]
        if estranhos:
            problemas.append(f"{chamada}: o parâmetro {', '.join(estranhos)} não existe. "
                             f"Os parâmetros são: {', '.join(nomes)}.")
        dados = set(nomes[:len(n.args)]) | {k.arg for k in n.keywords}
        faltando = [p for p in obrigatorios if p not in dados]
        if faltando:
            problemas.append(f"{chamada}: falta {', '.join(faltando)}.")
    return problemas


def problemas_da_cena(cena, tol=0.02):
    """Peças abaixo do chão ou flutuando, pela medição de `Blender.medir_cena()`.

    Uma peça está apoiada se a base encosta no chão, no topo de outra peça ou fica dentro
    da altura de outra (encaixe, como a perna que entra no tampo), e as duas se cruzam em X e Y.
    ponytail: compara caixas alinhadas aos eixos; uma peça girada ocupa uma caixa maior que ela.
    """
    malhas = cena["malhas"]
    if not malhas:
        return ["A cena ficou vazia: nenhuma peça foi criada. Não chame limpar_cena() no fim do código."]
    problemas = []
    for m in malhas:
        if m["z_min"] < -tol:
            problemas.append(f"'{m['nome']}' está abaixo do chão: a base está em z = {m['z_min']}.")
            continue
        outras = [o for o in malhas if o is not m]
        apoiada = (abs(m["z_min"]) <= tol
                   or any(o["z_min"] - tol <= m["z_min"] <= o["z_max"] + tol and se_cruzam(m, o, tol)
                          for o in outras))
        if not apoiada:
            problemas.append(f"'{m['nome']}' está flutuando: a base está em z = {m['z_min']} "
                             "e não encosta no chão nem em outra peça.")
    return problemas


def se_cruzam(a, b, tol):
    """As caixas de a e b se encostam ou se sobrepõem no plano XY."""
    ax0, ax1, ay0, ay1 = a["xy"]
    bx0, bx1, by0, by1 = b["xy"]
    return ax0 <= bx1 + tol and bx0 <= ax1 + tol and ay0 <= by1 + tol and by0 <= ay1 + tol


BLOCO_PYTHON = re.compile(r"```(?:python|py)[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)


def codigo_no_texto(texto):
    """Separa o primeiro bloco ```python do texto: (código, texto sem o bloco) ou (None, texto)."""
    achado = BLOCO_PYTHON.search(texto)
    if achado:
        return achado.group(1).strip(), (texto[:achado.start()] + texto[achado.end():]).strip()
    # O qwen3.5:4b às vezes escreve o código solto, sem ``` ("Passo 1, code:\nimport bpy..."): pega de
    # "import bpy" em diante e tira as linhas do fim até compilar (texto que veio depois do código).
    solto = re.search(r"^import bpy\b.*", texto, re.S | re.M)
    if solto:
        linhas = solto.group(0).splitlines()
        while linhas:
            try:
                ast.parse("\n".join(linhas))
                return "\n".join(linhas).strip(), texto[:solto.start()].strip()
            except SyntaxError:
                linhas.pop()
    return None, texto.strip()


def mensagem_de_erro(saida):
    """Tira a mensagem legível do JSON de erro do Blender."""
    try:
        dados = json.loads(saida)
        return str(dados.get("message") or dados)[:800]
    except (ValueError, AttributeError):
        return saida[:800]
