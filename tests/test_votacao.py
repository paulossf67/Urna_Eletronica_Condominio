"""Fluxo de votação: um voto por cargo, atomicidade e apuração."""

import threading

import pytest

import database
import eleicao
from eleicao import (
    ErroApuracao,
    alterar_status_eleicao,
    autenticar_eleitor,
    cargos_pendentes,
    cargos_votados,
    listar_cargos,
    obter_candidato_por_numero,
    obter_resultados,
    registrar_voto,
    verificar_recibo,
)

CPF1 = "123.456.789-09"
CPF2 = "234.567.890-92"


def _eleitor(eid, cpf):
    return autenticar_eleitor(eid, cpf)


# ---------- um voto por cargo ----------

def test_eleicao_demo_tem_dois_cargos(eleicao_demo):
    assert listar_cargos(eleicao_demo) == ["Conselheiro", "Síndico"]


def test_eleitor_vota_uma_vez_em_cada_cargo(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    assert sorted(e["cargos_pendentes"]) == ["Conselheiro", "Síndico"]

    registrar_voto(eleicao_demo, e["id"], "Síndico", "candidato",
                   obter_candidato_por_numero(eleicao_demo, 10, "Síndico")["id"])
    assert cargos_pendentes(eleicao_demo, e["id"]) == ["Conselheiro"]

    registrar_voto(eleicao_demo, e["id"], "Conselheiro", "candidato",
                   obter_candidato_por_numero(eleicao_demo, 30, "Conselheiro")["id"])
    assert cargos_pendentes(eleicao_demo, e["id"]) == []
    assert sorted(cargos_votados(eleicao_demo, e["id"])) == ["Conselheiro", "Síndico"]


def test_ja_votou_so_fica_1_apos_todos_os_cargos(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")
    assert eleicao.obter_eleitor(e["id"])["ja_votou"] == 0

    registrar_voto(eleicao_demo, e["id"], "Conselheiro", "branco")
    assert eleicao.obter_eleitor(e["id"])["ja_votou"] == 1


def test_voto_duplo_no_mesmo_cargo_e_recusado(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")
    with pytest.raises(ValueError, match="já votou para o cargo"):
        registrar_voto(eleicao_demo, e["id"], "Síndico", "nulo")

    assert eleicao.contar_votos(eleicao_demo) == 1


def test_cargo_inexistente_e_recusado(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    with pytest.raises(ValueError, match="Cargo inválido"):
        registrar_voto(eleicao_demo, e["id"], "Tesoureiro", "branco")


def test_candidato_de_outro_cargo_e_recusado(eleicao_demo):
    """Não se pode votar no candidato a Conselheiro na cédula de Síndico."""
    e = _eleitor(eleicao_demo, CPF1)
    conselheiro = obter_candidato_por_numero(eleicao_demo, 30, "Conselheiro")
    with pytest.raises(ValueError, match="não pertence a este cargo"):
        registrar_voto(eleicao_demo, e["id"], "Síndico", "candidato", conselheiro["id"])


def test_voto_em_eleicao_fechada_e_recusado(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    alterar_status_eleicao(eleicao_demo, "fechada")
    with pytest.raises(ValueError, match="não está aberta"):
        registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")


def test_eleitor_de_outra_eleicao_e_recusado(eleicao_demo, eleicao_simples):
    outro = _eleitor(eleicao_simples, CPF1)
    with pytest.raises(ValueError, match="Eleitor inválido"):
        registrar_voto(eleicao_demo, outro["id"], "Síndico", "branco")


# ---------- atomicidade sob concorrência ----------

def test_votos_simultaneos_no_mesmo_cargo_gravam_apenas_um(eleicao_simples):
    """
    Duas threads tentam votar ao mesmo tempo pelo mesmo eleitor/cargo.
    A UNIQUE de `participacao` dentro do BEGIN IMMEDIATE tem que deixar
    exatamente um voto passar.
    """
    e = _eleitor(eleicao_simples, CPF1)
    barreira = threading.Barrier(2)
    resultados: list[str] = []
    trava = threading.Lock()

    def tentar():
        barreira.wait()
        try:
            registrar_voto(eleicao_simples, e["id"], "Presidente", "branco")
            with trava:
                resultados.append("ok")
        except ValueError as exc:
            with trava:
                resultados.append(f"erro: {exc}")

    threads = [threading.Thread(target=tentar) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert resultados.count("ok") == 1, resultados
    assert eleicao.contar_votos(eleicao_simples) == 1

    with database.db_session() as conn:
        n = conn.execute(
            "SELECT COUNT(*) t FROM participacao WHERE eleitor_id = ?", (e["id"],)
        ).fetchone()["t"]
    assert n == 1


def test_varios_eleitores_em_paralelo(eleicao_simples):
    """Eleitores diferentes votando ao mesmo tempo: todos os votos entram."""
    eleitores = [_eleitor(eleicao_simples, c)
                 for c in ("123.456.789-09", "234.567.890-92", "345.678.901-75")]
    barreira = threading.Barrier(len(eleitores))
    erros: list[Exception] = []

    def votar(el):
        barreira.wait()
        try:
            registrar_voto(eleicao_simples, el["id"], "Presidente", "branco")
        except Exception as exc:  # noqa: BLE001 - o teste quer ver qualquer falha
            erros.append(exc)

    threads = [threading.Thread(target=votar, args=(el,)) for el in eleitores]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not erros, erros
    assert eleicao.contar_votos(eleicao_simples) == 3


# ---------- sigilo ----------

def test_voto_gravado_nao_referencia_o_eleitor(eleicao_simples):
    e = _eleitor(eleicao_simples, CPF1)
    cand = obter_candidato_por_numero(eleicao_simples, 11, "Presidente")
    registrar_voto(eleicao_simples, e["id"], "Presidente", "candidato", cand["id"])

    with database.db_session() as conn:
        voto = dict(conn.execute("SELECT * FROM votos").fetchone())
        colunas = [r[1] for r in conn.execute("PRAGMA table_info(votos)").fetchall()]

    # A tabela de votos não tem nenhuma coluna que aponte para o eleitor
    assert not any("eleitor" in c for c in colunas)
    # E as colunas em claro do voto ficam vazias: a escolha só existe cifrada
    assert voto["candidato_id"] is None
    assert voto["tipo_voto"] is None
    assert voto["voto_cifrado"] and voto["compromisso"]

    # Só quem tem a chave abre o voto
    from crypto_voto import decifrar_voto
    aberto = decifrar_voto(voto["voto_cifrado"], voto["integridade"])
    assert aberto["candidato_id"] == cand["id"]


def test_dois_votos_identicos_ficam_diferentes_no_banco(eleicao_simples):
    """Sem isso daria para contar votos iguais só comparando os ciphertexts."""
    cand = obter_candidato_por_numero(eleicao_simples, 11, "Presidente")
    for cpf in (CPF1, "234.567.890-92"):
        e = _eleitor(eleicao_simples, cpf)
        registrar_voto(eleicao_simples, e["id"], "Presidente", "candidato", cand["id"])

    with database.db_session() as conn:
        linhas = conn.execute("SELECT voto_cifrado, compromisso FROM votos").fetchall()

    assert linhas[0]["voto_cifrado"] != linhas[1]["voto_cifrado"]
    assert linhas[0]["compromisso"] != linhas[1]["compromisso"]


def test_log_de_auditoria_nao_revela_a_escolha(eleicao_simples):
    e = _eleitor(eleicao_simples, CPF1)
    cand = obter_candidato_por_numero(eleicao_simples, 11, "Presidente")
    registrar_voto(eleicao_simples, e["id"], "Presidente", "candidato", cand["id"])

    regs = database.consultar_auditoria(acao="VOTO_REGISTRADO")
    assert len(regs) == 1
    detalhes = regs[0]["detalhes"]
    assert "Presidente" in detalhes          # o cargo é público
    assert "Candidata A" not in detalhes     # a escolha não
    assert f"candidato_id={cand['id']}" not in detalhes


# ---------- apuração ----------

def test_apuracao_conta_por_cargo(eleicao_demo):
    sindico_10 = obter_candidato_por_numero(eleicao_demo, 10, "Síndico")
    consel_30 = obter_candidato_por_numero(eleicao_demo, 30, "Conselheiro")

    for cpf in (CPF1, CPF2):
        e = _eleitor(eleicao_demo, cpf)
        registrar_voto(eleicao_demo, e["id"], "Síndico", "candidato", sindico_10["id"])
        registrar_voto(eleicao_demo, e["id"], "Conselheiro", "candidato", consel_30["id"])

    r = obter_resultados(eleicao_demo)
    assert r["total_votos"] == 4
    assert r["votaram"] == 2
    assert r["concluiram_todos_cargos"] == 2

    por_id = {c["id"]: c for c in r["candidatos"]}
    assert por_id[sindico_10["id"]]["votos"] == 2
    assert por_id[consel_30["id"]]["votos"] == 2
    # cada cargo recebeu exatamente 2 votos
    assert r["agregado_cargo"]["Síndico"]["total_votos"] == 2
    assert r["agregado_cargo"]["Conselheiro"]["total_votos"] == 2


def test_peso_ponderado_e_respeitado(eleicao_demo):
    """Carlos pesa 1.0, Fernanda 1.5 — o peso conta, não a cabeça."""
    cand = obter_candidato_por_numero(eleicao_demo, 10, "Síndico")
    for cpf in (CPF1, CPF2):
        e = _eleitor(eleicao_demo, cpf)
        registrar_voto(eleicao_demo, e["id"], "Síndico", "candidato", cand["id"])

    r = obter_resultados(eleicao_demo)
    assert r["ponderado"] is True
    alvo = next(c for c in r["candidatos"] if c["id"] == cand["id"])
    assert alvo["votos"] == 2
    assert alvo["peso_votos"] == pytest.approx(2.5)


def test_brancos_e_nulos_separados_por_cargo(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")
    registrar_voto(eleicao_demo, e["id"], "Conselheiro", "nulo")

    r = obter_resultados(eleicao_demo)
    assert r["agregado_cargo"]["Síndico"]["brancos"]["qtd"] == 1
    assert r["agregado_cargo"]["Síndico"]["nulos"]["qtd"] == 0
    assert r["agregado_cargo"]["Conselheiro"]["nulos"]["qtd"] == 1
    assert r["brancos"]["qtd"] == 1
    assert r["nulos"]["qtd"] == 1


def test_abstencao_conta_quem_nao_votou(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")
    r = obter_resultados(eleicao_demo)
    assert r["total_eleitores"] == 6
    assert r["votaram"] == 1
    assert r["abstencoes"] == 5
    assert r["concluiram_todos_cargos"] == 0


def test_apuracao_estrita_falha_com_voto_corrompido(eleicao_simples):
    """Perder a chave ou corromper dados tem que ser um ERRO, não um nulo."""
    e = _eleitor(eleicao_simples, CPF1)
    registrar_voto(eleicao_simples, e["id"], "Presidente", "branco")

    with database.db_session() as conn:
        conn.execute("UPDATE votos SET voto_cifrado = 'gAAAAAlixo'")

    with pytest.raises(ErroApuracao):
        obter_resultados(eleicao_simples)

    # Modo não estrito: apura o resto e reporta a falha explicitamente
    r = obter_resultados(eleicao_simples, estrito=False)
    assert len(r["falhas_decifra"]) == 1
    assert r["nulos"]["qtd"] == 0   # não foi transformado em nulo silenciosamente


# ---------- integridade ----------

def test_integridade_bate_apos_votacao(eleicao_demo):
    for cpf in (CPF1, CPF2):
        e = _eleitor(eleicao_demo, cpf)
        for cargo in ("Síndico", "Conselheiro"):
            registrar_voto(eleicao_demo, e["id"], cargo, "branco")

    d = database.auditoria_consistencia_votos(eleicao_demo)
    assert d["participacoes"] == 4
    assert d["votos_urna"] == 4
    assert d["eventos_log"] == 4
    assert d["consistente"] is True


def test_provas_or_de_todos_os_votos_sao_validas(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    registrar_voto(eleicao_demo, e["id"], "Síndico", "candidato",
                   obter_candidato_por_numero(eleicao_demo, 20, "Síndico")["id"])
    registrar_voto(eleicao_demo, e["id"], "Conselheiro", "branco")

    rel = eleicao.verificar_provas_or(eleicao_demo)
    assert rel["ok"] is True
    assert rel["com_prova_or"] == 1      # só o nominal tem prova OR
    assert rel["provas_validas"] == 1
    assert len(rel["sem_prova_or"]) == 1  # o branco não tem


def test_verificacao_zk_aprova_votos_integros(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")
    registrar_voto(eleicao_demo, e["id"], "Conselheiro", "nulo")

    rel = eleicao.verificar_votos_zk(eleicao_demo)
    assert rel["ok"] is True
    assert rel["total_votos"] == 2
    assert not rel["falhas_decifra"]


# ---------- recibo ----------

def test_recibo_e_encontrado_no_quadro(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    voto_id = registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")
    recibo = eleicao.obter_recibo_voto(voto_id)

    v = verificar_recibo(eleicao_demo, recibo["compromisso"])
    assert v["encontrado"] is True
    assert v["prova_valida"] is True
    assert v["cargo"] == "Síndico"


def test_recibo_falso_nao_e_encontrado(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")
    v = verificar_recibo(eleicao_demo, "f" * 64)
    assert v["encontrado"] is False


def test_recibo_nao_expoe_a_prova_or(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    cand = obter_candidato_por_numero(eleicao_demo, 10, "Síndico")
    voto_id = registrar_voto(eleicao_demo, e["id"], "Síndico", "candidato", cand["id"])
    recibo = eleicao.obter_recibo_voto(voto_id)
    assert recibo["tem_prova_or"] is True
    assert "prova_or" not in recibo


# ---------- contadores homomórficos ----------

def test_reabrir_eleicao_nao_zera_contadores_homomorficos(eleicao_simples):
    """Regressão: reabrir recriava Enc(0) e apagava a soma cifrada."""
    e = _eleitor(eleicao_simples, CPF1)
    cand = obter_candidato_por_numero(eleicao_simples, 11, "Presidente")
    registrar_voto(eleicao_simples, e["id"], "Presidente", "candidato", cand["id"])

    antes = eleicao.obter_resultados_homo(eleicao_simples)
    alvo_antes = next(c for c in antes["por_cargo"]["Presidente"]["candidatos"]
                      if c["id"] == cand["id"])
    assert alvo_antes["peso_votos"] == pytest.approx(1.0)

    alterar_status_eleicao(eleicao_simples, "fechada")
    alterar_status_eleicao(eleicao_simples, "aberta")

    depois = eleicao.obter_resultados_homo(eleicao_simples)
    alvo_depois = next(c for c in depois["por_cargo"]["Presidente"]["candidatos"]
                       if c["id"] == cand["id"])
    assert alvo_depois["peso_votos"] == pytest.approx(1.0)


def test_apuracao_homomorfica_bate_com_a_normal(eleicao_simples):
    cand = obter_candidato_por_numero(eleicao_simples, 11, "Presidente")
    for cpf in ("123.456.789-09", "234.567.890-92"):
        e = _eleitor(eleicao_simples, cpf)
        registrar_voto(eleicao_simples, e["id"], "Presidente", "candidato", cand["id"])

    normal = obter_resultados(eleicao_simples)
    homo = eleicao.obter_resultados_homo(eleicao_simples)

    peso_normal = next(c for c in normal["candidatos"] if c["id"] == cand["id"])["peso_votos"]
    peso_homo = next(c for c in homo["por_cargo"]["Presidente"]["candidatos"]
                     if c["id"] == cand["id"])["peso_votos"]
    assert peso_normal == pytest.approx(peso_homo)


# ---------- regras de status ----------

def test_nao_volta_para_preparacao_com_votos(eleicao_simples):
    e = _eleitor(eleicao_simples, CPF1)
    registrar_voto(eleicao_simples, e["id"], "Presidente", "branco")
    with pytest.raises(ValueError, match="já existem votos"):
        alterar_status_eleicao(eleicao_simples, "preparacao")


def test_nao_exclui_eleicao_com_votos(eleicao_simples):
    e = _eleitor(eleicao_simples, CPF1)
    registrar_voto(eleicao_simples, e["id"], "Presidente", "branco")
    with pytest.raises(ValueError, match="não pode ser excluída"):
        eleicao.excluir_eleicao(eleicao_simples)


def test_nao_exclui_eleitor_que_votou(eleicao_simples):
    e = _eleitor(eleicao_simples, CPF1)
    registrar_voto(eleicao_simples, e["id"], "Presidente", "branco")
    with pytest.raises(ValueError, match="já votou"):
        eleicao.excluir_eleitor(e["id"])


def test_nao_altera_peso_de_quem_votou(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")
    with pytest.raises(ValueError, match="peso de quem já votou"):
        eleicao.atualizar_eleitor(e["id"], peso=9.0)


def test_nao_troca_cpf_de_quem_votou(eleicao_demo):
    e = _eleitor(eleicao_demo, CPF1)
    registrar_voto(eleicao_demo, e["id"], "Síndico", "branco")
    with pytest.raises(ValueError, match="trocar o CPF"):
        eleicao.atualizar_eleitor(e["id"], documento="987.654.321-00")
