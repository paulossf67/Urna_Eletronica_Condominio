"""
Configuração dos testes.

Redireciona banco, chaves e segredos para um diretório temporário ANTES de
qualquer import dos módulos da urna — todos leem os caminhos do ambiente na
hora do import. Assim os testes nunca tocam no urna.db real.
"""

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="urna_testes_")

os.environ["URNA_DB"] = os.path.join(_TMP, "urna_teste.db")
os.environ["URNA_KEY"] = os.path.join(_TMP, "urna_teste.key")
os.environ["URNA_PAILLIER"] = os.path.join(_TMP, "paillier_teste.json")
os.environ["URNA_REMOTO_SECRET"] = os.path.join(_TMP, "remoto_teste.secret")
# Chave pequena: os testes exercitam o protocolo, não a força da chave
os.environ["URNA_PAILLIER_BITS"] = "512"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import database  # noqa: E402
import eleicao  # noqa: E402


@pytest.fixture(autouse=True)
def banco_limpo():
    """Recria o banco do zero antes de cada teste."""
    for sufixo in ("", "-wal", "-shm"):
        caminho = database.DB_PATH + sufixo
        if os.path.isfile(caminho):
            os.remove(caminho)
    database.inicializar_banco()
    yield


@pytest.fixture
def eleicao_demo():
    """Eleição de demonstração já aberta, com 2 cargos e 6 eleitores."""
    eid = eleicao.criar_eleicao_demonstracao()
    eleicao.alterar_status_eleicao(eid, "aberta")
    return eid


@pytest.fixture
def eleicao_simples():
    """Eleição não ponderada, 1 cargo, 2 candidatos, 3 eleitores. Aberta."""
    eid = eleicao.criar_eleicao("Teste Simples", "", "associacao", voto_ponderado=False)
    eleicao.cadastrar_candidato(eid, 11, "Candidata A", "Presidente")
    eleicao.cadastrar_candidato(eid, 22, "Candidato B", "Presidente")
    for i, cpf in enumerate(("123.456.789-09", "234.567.890-92", "345.678.901-75")):
        eleicao.cadastrar_eleitor(eid, f"Eleitor {i}", cpf)
    eleicao.alterar_status_eleicao(eid, "aberta")
    return eid


def eleitor_por_cpf(eid: int, cpf: str) -> dict:
    return eleicao.autenticar_eleitor(eid, cpf)
