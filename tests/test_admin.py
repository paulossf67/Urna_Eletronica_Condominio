"""Cadastros, edição, importação CSV, backup e restauração."""

import os

import pytest

import database
import eleicao


# ---------- cadastros ----------

def test_cpf_invalido_e_recusado_no_cadastro():
    eid = eleicao.criar_eleicao("X", "", "condominio")
    with pytest.raises(ValueError, match="CPF inválido"):
        eleicao.cadastrar_eleitor(eid, "Fulano", "111.111.111-11")


def test_cpf_duplicado_na_mesma_eleicao_e_recusado():
    eid = eleicao.criar_eleicao("X", "", "condominio")
    eleicao.cadastrar_eleitor(eid, "Fulano", "123.456.789-09")
    with pytest.raises(ValueError, match="já está cadastrado"):
        eleicao.cadastrar_eleitor(eid, "Outro", "12345678909")


def test_mesmo_cpf_em_eleicoes_diferentes_e_permitido():
    a = eleicao.criar_eleicao("A", "", "condominio")
    b = eleicao.criar_eleicao("B", "", "condominio")
    eleicao.cadastrar_eleitor(a, "Fulano", "123.456.789-09")
    eleicao.cadastrar_eleitor(b, "Fulano", "123.456.789-09")


def test_peso_zero_ou_negativo_e_recusado():
    eid = eleicao.criar_eleicao("X", "", "condominio", voto_ponderado=True)
    for peso in (0, -1.5):
        with pytest.raises(ValueError, match="maior que zero"):
            eleicao.cadastrar_eleitor(eid, "Fulano", "123.456.789-09", peso=peso)


def test_numero_de_candidato_duplicado_e_recusado():
    eid = eleicao.criar_eleicao("X", "", "condominio")
    eleicao.cadastrar_candidato(eid, 10, "A", "Síndico")
    with pytest.raises(ValueError, match="Já existe candidato"):
        eleicao.cadastrar_candidato(eid, 10, "B", "Conselheiro")


def test_candidato_so_entra_em_preparacao(eleicao_simples):
    with pytest.raises(ValueError, match="em preparação"):
        eleicao.cadastrar_candidato(eleicao_simples, 99, "Tardio", "Presidente")


def test_abrir_sem_candidato_gera_eleicao_sem_cargos():
    eid = eleicao.criar_eleicao("Vazia", "", "condominio")
    assert eleicao.listar_cargos(eid) == []


# ---------- edição ----------

def test_editar_eleicao():
    eid = eleicao.criar_eleicao("Nome Velho", "desc", "condominio")
    assert eleicao.atualizar_eleicao(eid, nome="Nome Novo")
    assert eleicao.obter_eleicao(eid)["nome"] == "Nome Novo"


def test_nao_muda_ponderado_com_votos(eleicao_simples):
    e = eleicao.autenticar_eleitor(eleicao_simples, "123.456.789-09")
    eleicao.registrar_voto(eleicao_simples, e["id"], "Presidente", "branco")
    with pytest.raises(ValueError, match="modo ponderado"):
        eleicao.atualizar_eleicao(eleicao_simples, voto_ponderado=True)


def test_editar_candidato():
    eid = eleicao.criar_eleicao("X", "", "condominio")
    cid = eleicao.cadastrar_candidato(eid, 10, "Nome Velho", "Síndico")
    assert eleicao.atualizar_candidato(cid, nome="Nome Novo", numero=15)
    c = eleicao.obter_candidato(cid)
    assert c["nome"] == "Nome Novo"
    assert c["numero"] == 15


def test_excluir_candidato_em_preparacao_remove_de_fato():
    eid = eleicao.criar_eleicao("X", "", "condominio")
    cid = eleicao.cadastrar_candidato(eid, 10, "A", "Síndico")
    eleicao.excluir_candidato(cid)
    assert eleicao.obter_candidato(cid) is None


def test_excluir_candidato_com_votos_apenas_desativa(eleicao_simples):
    e = eleicao.autenticar_eleitor(eleicao_simples, "123.456.789-09")
    eleicao.registrar_voto(eleicao_simples, e["id"], "Presidente", "branco")
    cand = eleicao.obter_candidato_por_numero(eleicao_simples, 11, "Presidente")
    eleicao.excluir_candidato(cand["id"])
    assert eleicao.obter_candidato(cand["id"])["ativo"] == 0


def test_editar_eleitor_antes_de_votar(eleicao_simples):
    e = eleicao.autenticar_eleitor(eleicao_simples, "123.456.789-09")
    assert eleicao.atualizar_eleitor(e["id"], nome="Nome Corrigido", unidade="777")
    atualizado = eleicao.obter_eleitor(e["id"])
    assert atualizado["nome"] == "Nome Corrigido"
    assert atualizado["unidade"] == "777"


def test_excluir_eleitor_que_nao_votou(eleicao_simples):
    e = eleicao.autenticar_eleitor(eleicao_simples, "123.456.789-09")
    eleicao.excluir_eleitor(e["id"])
    assert eleicao.obter_eleitor(e["id"]) is None


# ---------- importação CSV ----------

def _escrever(tmp_path, nome, conteudo):
    caminho = tmp_path / nome
    caminho.write_text(conteudo, encoding="utf-8")
    return str(caminho)


def test_importar_csv_ponto_e_virgula(tmp_path):
    eid = eleicao.criar_eleicao("X", "", "condominio", voto_ponderado=True)
    csv = _escrever(tmp_path, "e.csv", (
        "nome;cpf;unidade;bloco;peso\n"
        "Carlos Mendes;123.456.789-09;101;A;1.0\n"
        "Fernanda Lima;234.567.890-92;202;A;1,5\n"
    ))
    rel = eleicao.importar_eleitores_csv(eid, csv)
    assert rel["importados"] == 2
    assert rel["total_erros"] == 0
    eleitores = {e["nome"]: e for e in eleicao.listar_eleitores(eid)}
    assert eleitores["Fernanda Lima"]["peso"] == 1.5


def test_importar_csv_virgula(tmp_path):
    eid = eleicao.criar_eleicao("X", "", "condominio")
    csv = _escrever(tmp_path, "e.csv", "nome,cpf\nCarlos,123.456.789-09\n")
    assert eleicao.importar_eleitores_csv(eid, csv)["importados"] == 1


def test_importar_csv_aceita_cabecalho_com_acento_e_maiuscula(tmp_path):
    eid = eleicao.criar_eleicao("X", "", "condominio")
    csv = _escrever(tmp_path, "e.csv", "NOME;CPF;Unidade\nCarlos;123.456.789-09;10\n")
    assert eleicao.importar_eleitores_csv(eid, csv)["importados"] == 1


def test_importar_csv_reporta_linhas_ruins_sem_parar(tmp_path):
    eid = eleicao.criar_eleicao("X", "", "condominio")
    csv = _escrever(tmp_path, "e.csv", (
        "nome;cpf\n"
        "Bom;123.456.789-09\n"
        "CpfRuim;111.111.111-11\n"
        "Duplicado;123.456.789-09\n"
        "Outro Bom;234.567.890-92\n"
    ))
    rel = eleicao.importar_eleitores_csv(eid, csv)
    assert rel["importados"] == 2
    assert rel["total_erros"] == 2
    assert {e["nome"] for e in rel["erros"]} == {"CpfRuim", "Duplicado"}


def test_importar_csv_sem_colunas_obrigatorias(tmp_path):
    eid = eleicao.criar_eleicao("X", "", "condominio")
    csv = _escrever(tmp_path, "e.csv", "apelido;telefone\nZe;999\n")
    with pytest.raises(ValueError, match="colunas 'nome' e 'cpf'"):
        eleicao.importar_eleitores_csv(eid, csv)


def test_importar_arquivo_inexistente():
    eid = eleicao.criar_eleicao("X", "", "condominio")
    with pytest.raises(FileNotFoundError):
        eleicao.importar_eleitores_csv(eid, "nao_existe_mesmo.csv")


def test_modelo_csv_pode_ser_reimportado(tmp_path):
    eid = eleicao.criar_eleicao("X", "", "condominio", voto_ponderado=True)
    modelo = eleicao.modelo_csv_eleitores(str(tmp_path / "modelo.csv"))
    assert eleicao.importar_eleitores_csv(eid, modelo)["importados"] == 2


# ---------- backup e restauração ----------

def test_backup_e_restauracao_preservam_os_votos(eleicao_simples):
    e = eleicao.autenticar_eleitor(eleicao_simples, "123.456.789-09")
    eleicao.registrar_voto(eleicao_simples, e["id"], "Presidente", "branco")
    assert eleicao.contar_votos(eleicao_simples) == 1

    copia = database.fazer_backup("teste")
    assert os.path.isfile(copia)

    # Mais um voto depois do backup
    e2 = eleicao.autenticar_eleitor(eleicao_simples, "234.567.890-92")
    eleicao.registrar_voto(eleicao_simples, e2["id"], "Presidente", "nulo")
    assert eleicao.contar_votos(eleicao_simples) == 2

    database.restaurar_backup(copia)
    assert eleicao.contar_votos(eleicao_simples) == 1


def test_backup_captura_o_wal(eleicao_simples):
    """A cópia precisa incluir páginas ainda no WAL, não só o .db."""
    e = eleicao.autenticar_eleitor(eleicao_simples, "123.456.789-09")
    eleicao.registrar_voto(eleicao_simples, e["id"], "Presidente", "branco")

    import sqlite3
    copia = database.fazer_backup("wal")
    conn = sqlite3.connect(copia)
    try:
        total = conn.execute("SELECT COUNT(*) FROM votos").fetchone()[0]
    finally:
        conn.close()
    assert total == 1


def test_restaurar_recusa_arquivo_que_nao_e_banco_da_urna(tmp_path):
    import sqlite3
    falso = tmp_path / "falso.db"
    conn = sqlite3.connect(str(falso))
    conn.execute("CREATE TABLE qualquer (x INTEGER)")
    conn.commit()
    conn.close()

    with pytest.raises(ValueError, match="não parece ser um banco da urna"):
        database.restaurar_backup(str(falso))


def test_restaurar_arquivo_inexistente():
    with pytest.raises(FileNotFoundError):
        database.restaurar_backup("nao_existe.db")


# ---------- exportação ----------

def test_exportar_resultados_csv(eleicao_demo, tmp_path):
    e = eleicao.autenticar_eleitor(eleicao_demo, "123.456.789-09")
    eleicao.registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")

    destino = str(tmp_path / "res.csv")
    eleicao.exportar_resultados_csv(eleicao_demo, destino)
    conteudo = open(destino, encoding="utf-8-sig").read()
    assert "RESULTADOS DA ELEIÇÃO" in conteudo
    assert "Síndico" in conteudo
    # o CSV de resultados não pode vazar CPF
    assert "123.456.789-09" not in conteudo


def test_exportar_quadro_nao_contem_dados_pessoais(eleicao_demo, tmp_path):
    e = eleicao.autenticar_eleitor(eleicao_demo, "123.456.789-09")
    eleicao.registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")

    destino = str(tmp_path / "quadro.csv")
    eleicao.exportar_quadro_compromissos(eleicao_demo, destino)
    conteudo = open(destino, encoding="utf-8-sig").read()
    assert "Raiz Merkle" in conteudo
    assert "123.456.789-09" not in conteudo
    assert "Carlos Mendes" not in conteudo


def test_relatorio_de_presenca_nao_revela_escolha(eleicao_demo):
    e = eleicao.autenticar_eleitor(eleicao_demo, "123.456.789-09")
    cand = eleicao.obter_candidato_por_numero(eleicao_demo, 10, "Síndico")
    eleicao.registrar_voto(eleicao_demo, e["id"], "Síndico", "candidato", cand["id"])

    rel = eleicao.relatorio_votos(eleicao_demo)
    texto = str(rel)
    assert rel["qtd_votaram"] == 1
    assert rel["qtd_parciais"] == 1
    assert "João Silva" not in texto      # nome do candidato escolhido


def test_relatorio_pode_mascarar_cpf(eleicao_demo):
    rel = eleicao.relatorio_votos(eleicao_demo, mascarar_cpf_saida=True)
    for e in rel["pendentes"]:
        assert "***" in e["cpf_formatado"]
