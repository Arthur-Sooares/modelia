import json
from types import SimpleNamespace

from modelia import avaliacao


def juiz_falso(nota):
    def conversar(modelo, mensagens, ferramentas=None, opcoes=None, formato=None):
        assert modelo == avaliacao.JUIZ and formato == avaliacao.FORMATO_JUIZ
        assert "Fontes" not in mensagens[1]["content"]
        conteudo = json.dumps({"justificativa": "ok", "nota": nota, "inventou": False})
        return SimpleNamespace(message=SimpleNamespace(content=conteudo))
    return conversar


def linha(fora=False, recusou=False, resposta="Use a extrusão, tecla E."):
    return {"condicao": "puro", "pergunta": "como puxo uma face?", "fora": fora, "recusou": recusou,
            "resposta": resposta}


def test_sem_fontes_esconde_a_condicao():
    assert avaliacao.sem_fontes("Use E.\n\n**Fontes:** Manual") == "Use E."


def test_recusa_do_modelia_e_reconhecida():
    assert avaliacao.recusou("O material da disciplina não cobre esse assunto.")
    assert not avaliacao.recusou("Use a extrusão.")


def test_fora_do_blender_vale_3_so_quando_recusa():
    nunca = juiz_falso(None)  # não pode ser chamado
    assert avaliacao.nota_da_linha(linha(fora=True, recusou=True), "", nunca)["nota"] == 3
    assert avaliacao.nota_da_linha(linha(fora=True), "", nunca)["nota"] == 1


def test_recusar_pergunta_do_blender_vale_0_e_o_resto_vai_ao_juiz():
    assert avaliacao.nota_da_linha(linha(recusou=True), "Extrusão", juiz_falso(None))["nota"] == 0
    assert avaliacao.nota_da_linha(linha(), "Extrusão", juiz_falso(2))["nota"] == 2


def test_resumo_separa_condicao_e_tipo():
    linhas = [{"condicao": "puro", "fora": False, "nota_0a3": 1, "inventou": True},
              {"condicao": "modelia", "fora": False, "nota_0a3": 3, "inventou": False},
              {"condicao": "modelia", "fora": True, "nota_0a3": 3, "inventou": False}]
    texto = avaliacao.resumo(linhas)
    assert "puro     blender  n=  1  nota média 1.00" in texto
    assert "modelia  fora" in texto
