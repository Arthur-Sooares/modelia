"""Testes da preparação do computador, sem internet e sem Ollama de verdade."""

import zipfile

import pytest

from modelia import config, manual, preparar


def test_so_baixa_os_modelos_que_faltam(monkeypatch):
    monkeypatch.setattr(preparar.llm, "modelos", lambda: [{"nome": "qwen3-embedding:0.6b"}])
    monkeypatch.setattr(config, "MODELOS_NECESSARIOS", ["qwen3.5:4b", "qwen3-embedding:0.6b"])
    baixados = []
    monkeypatch.setattr(preparar.llm, "baixar", lambda nome: baixados.append(nome) or iter([(0.5, "pulling")]))
    passos = list(preparar.baixar_modelos())
    assert baixados == ["qwen3.5:4b"]
    assert passos[0] == (0.5, "Modelo 1 de 1 (qwen3.5:4b): pulling") and passos[-1][0] == 1


def test_ollama_ligado_nao_reinstala(monkeypatch):
    monkeypatch.setattr(preparar.llm, "disponivel", lambda: True)
    assert list(preparar.instalar_ollama()) == [(1, "Ollama já está instalado e ligado.")]


def test_zip_que_nao_e_o_manual_e_recusado(tmp_path):
    errado = tmp_path / "outro.zip"
    with zipfile.ZipFile(errado, "w") as z:
        z.writestr("leia-me.txt", "nada")
    with pytest.raises(ValueError, match="não é o manual"):
        manual.usar_zip(errado, tmp_path / "manual")


def test_manual_ja_indexado_nao_baixa_de_novo(monkeypatch):
    class Base:
        def listar(self):
            return [{"titulo": f"{manual.TITULO}: Modificadores", "fonte": "https://docs.blender.org/manual/pt/5.0/m.html"}]

    monkeypatch.setattr(preparar, "baixar_arquivo", lambda *a: pytest.fail("não devia baixar"))
    assert list(preparar.preparar_manual(Base())) == [(1, "O manual do Blender já está na base.")]


PAGINA = ('<html><article role="main"><h1>Chanfro</h1><p>' + "O chanfro arredonda as bordas da malha. " * 10 +
          "</p></article></html>")


class BaseFalsa:
    def __init__(self):
        self.textos = []

    def listar(self):
        return []

    def adicionar_texto(self, texto, titulo, fonte=""):
        self.textos.append((titulo, fonte))
        return 1


def montar_manual(raiz, nome_inicial):
    for secao in ("modeling/meshes/editing", "interface"):
        (raiz / secao).mkdir(parents=True)
    (raiz / "modeling/meshes/editing/bevel.html").write_text(PAGINA, encoding="utf-8")
    (raiz / nome_inicial).write_text("<html></html>", encoding="utf-8")


def test_acha_o_manual_na_pasta_extraida_e_a_versao(tmp_path):
    montar_manual(tmp_path / "blender_manual_html", "blender_manual_v500_pt.html")
    raiz, versao, idioma = manual.achar_pasta(f'"{tmp_path}"')  # aceita o caminho entre aspas e a pasta de cima
    assert raiz == tmp_path / "blender_manual_html" and (versao, idioma) == ("5.0", "pt")
    base = BaseFalsa()
    passos = list(preparar.preparar_manual(base, pasta_propria=str(raiz)))
    assert base.textos == [(f"{manual.TITULO}: Chanfro",
                            "https://docs.blender.org/manual/pt/5.0/modeling/meshes/editing/bevel.html")]
    assert "o do curso é o 5.1" in passos[-1][1]


def test_pasta_que_nao_e_o_manual_e_recusada(tmp_path):
    with pytest.raises(ValueError, match="Não achei a pasta"):
        manual.achar_pasta(tmp_path / "nao-existe")
    (tmp_path / "fotos").mkdir()
    with pytest.raises(ValueError, match="não parece o manual"):
        manual.achar_pasta(tmp_path)


def test_manual_embutido_indexa_sem_internet(monkeypatch, tmp_path):
    montar_manual(tmp_path, "blender_manual_v510_pt.html")
    embutido = tmp_path / "manual.json.gz"
    assert manual.embutir(tmp_path, embutido) == 1
    monkeypatch.setattr(manual, "EMBUTIDO", embutido)
    monkeypatch.setattr(preparar, "baixar_arquivo", lambda *a: pytest.fail("não devia baixar"))
    base = BaseFalsa()
    list(preparar.preparar_manual(base))
    assert base.textos == [(f"{manual.TITULO}: Chanfro", manual.ENDERECO + "modeling/meshes/editing/bevel.html")]


def test_manual_embutido_do_projeto_esta_completo():
    paginas = manual.paginas_embutidas()
    assert len(paginas) > 300 and all(link.startswith(manual.ENDERECO) for _, _, link in paginas)


def test_manual_que_parou_no_meio_continua_de_onde_parou(monkeypatch, tmp_path):
    for nome in ("bevel", "extrude"):
        montar = tmp_path / "modeling/meshes/editing"
        montar.mkdir(parents=True, exist_ok=True)
        (montar / f"{nome}.html").write_text(PAGINA.replace("Chanfro", nome), encoding="utf-8")
    (tmp_path / "interface").mkdir()
    embutido = tmp_path / "manual.json.gz"
    manual.embutir(tmp_path, embutido)
    monkeypatch.setattr(manual, "EMBUTIDO", embutido)

    class BaseParcial(BaseFalsa):
        def listar(self):
            link = manual.ENDERECO + "modeling/meshes/editing/bevel.html"
            return [{"titulo": f"{manual.TITULO}: bevel", "fonte": link}]

    base = BaseParcial()
    assert not preparar.manual_indexado(base)  # parou no meio: não conta como pronto
    passos = list(preparar.preparar_manual(base))
    assert passos[0][1] == "Continuando o manual: faltam 1 páginas…"
    assert base.textos == [(f"{manual.TITULO}: extrude", manual.ENDERECO + "modeling/meshes/editing/extrude.html")]
