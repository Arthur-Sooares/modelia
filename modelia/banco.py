"""Banco de dados local em SQLite (RF1, RF9, RNF10).

Cada escrita é gravada na hora (commit imediato), para que nada se perca
se o app for fechado no meio de uma sessão.
"""

import csv
import io
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from modelia import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS estudantes (
    identificador TEXT PRIMARY KEY,
    criado_em     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    estudante     TEXT NOT NULL REFERENCES estudantes(identificador),
    inicio        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interacoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sessao_id     INTEGER NOT NULL REFERENCES sessoes(id),
    data          TEXT NOT NULL,
    modo          TEXT NOT NULL,
    modelo        TEXT NOT NULL,
    pedido        TEXT NOT NULL,
    resposta      TEXT NOT NULL DEFAULT '',
    fontes        TEXT NOT NULL DEFAULT '',
    tarefa        TEXT NOT NULL DEFAULT '',
    erros         INTEGER NOT NULL DEFAULT 0,
    segundos      REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS codigos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    interacao_id  INTEGER NOT NULL REFERENCES interacoes(id),
    data          TEXT NOT NULL,
    codigo        TEXT NOT NULL,
    decisao       TEXT NOT NULL,          -- 'executado', 'cancelado', 'automatico' ou 'recusado' (Demonstrar)
    resultado     TEXT NOT NULL DEFAULT '',
    erro          INTEGER NOT NULL DEFAULT 0
);
"""

# Sem espaços: impede que o aluno digite o nome completo (Resolução CNS 510/2016).
FORMATO_ID = re.compile(r"^[A-Za-z0-9_-]{2,20}$")


def agora():
    return datetime.now().isoformat(timespec="seconds")


def conectar(caminho=None):
    caminho = Path(caminho or config.BANCO)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(caminho)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


class Banco:
    """Acesso ao banco. Abre uma conexão por operação, o que é seguro com as várias threads do Gradio."""

    def __init__(self, caminho=None):
        self.caminho = caminho or config.BANCO
        with closing(conectar(self.caminho)) as conexao:
            conexao.executescript(ESQUEMA)

    def _executar(self, sql, parametros=()):
        with closing(conectar(self.caminho)) as conexao, conexao:
            return conexao.execute(sql, parametros).lastrowid

    def _consultar(self, sql, parametros=()):
        with closing(conectar(self.caminho)) as conexao:
            return [dict(linha) for linha in conexao.execute(sql, parametros)]

    # --- RF1: sessões ---

    def iniciar_sessao(self, identificador):
        """Cria a sessão e devolve o id dela. Levanta ValueError se o identificador for inválido."""
        identificador = (identificador or "").strip()
        if not identificador:
            raise ValueError("Digite seu código de participante para começar.")
        if not FORMATO_ID.match(identificador):
            raise ValueError("Use só o código de participante (ex.: P07), sem espaços e sem o nome completo.")
        self._executar(
            "INSERT OR IGNORE INTO estudantes (identificador, criado_em) VALUES (?, ?)",
            (identificador, agora()),
        )
        return self._executar(
            "INSERT INTO sessoes (estudante, inicio) VALUES (?, ?)", (identificador, agora())
        )

    # --- RNF10: interações e códigos ---

    def registrar_interacao(self, sessao_id, modo, modelo, pedido):
        return self._executar(
            "INSERT INTO interacoes (sessao_id, data, modo, modelo, pedido) VALUES (?, ?, ?, ?, ?)",
            (sessao_id, agora(), modo, modelo, pedido),
        )

    def concluir_interacao(self, interacao_id, resposta, segundos, erros=0, fontes="", tarefa=""):
        self._executar(
            "UPDATE interacoes SET resposta = ?, segundos = ?, erros = ?, fontes = ?, tarefa = ? WHERE id = ?",
            (resposta, round(segundos, 2), erros, fontes, tarefa, interacao_id),
        )

    def registrar_codigo(self, interacao_id, codigo, decisao, resultado="", erro=False):
        return self._executar(
            "INSERT INTO codigos (interacao_id, data, codigo, decisao, resultado, erro) VALUES (?, ?, ?, ?, ?, ?)",
            (interacao_id, agora(), codigo, decisao, resultado, int(erro)),
        )

    # --- RF9: histórico ---

    def historico(self, estudante="", modo="", desde="", ate=""):
        """Interações com filtros opcionais. `desde` e `ate` no formato AAAA-MM-DD."""
        condicoes, parametros = [], []
        if estudante:
            condicoes.append("s.estudante = ?")
            parametros.append(estudante)
        if modo:
            condicoes.append("i.modo = ?")
            parametros.append(modo)
        if desde:
            condicoes.append("i.data >= ?")
            parametros.append(desde)
        if ate:
            condicoes.append("i.data < date(?, '+1 day')")
            parametros.append(ate)
        onde = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""
        return self._consultar(
            f"""
            SELECT i.data, s.estudante, i.modo, i.modelo, i.pedido, i.resposta, i.tarefa, i.fontes,
                   (SELECT group_concat(c.codigo, char(10) || '# ---' || char(10))
                      FROM codigos c WHERE c.interacao_id = i.id
                        AND c.decisao IN ('executado', 'automatico')) AS codigo_executado,
                   i.erros, i.segundos
              FROM interacoes i JOIN sessoes s ON s.id = i.sessao_id
              {onde}
             ORDER BY i.data DESC, i.id DESC
            """,
            parametros,
        )

    def estudantes(self):
        return [linha["identificador"] for linha in self._consultar(
            "SELECT identificador FROM estudantes ORDER BY identificador")]


def para_csv(linhas):
    """Converte uma lista de dicionários em texto CSV (RF9, RF10)."""
    if not linhas:
        return ""
    saida = io.StringIO()
    escritor = csv.DictWriter(saida, fieldnames=list(linhas[0]))
    escritor.writeheader()
    escritor.writerows(linhas)
    return saida.getvalue()
