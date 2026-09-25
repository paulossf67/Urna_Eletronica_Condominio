"""
Módulo de gerenciamento de eleições, candidatos, eleitores e resultados.

Modelo de votação
-----------------
Uma eleição tem um ou mais CARGOS (ex: Síndico, Conselheiro). O eleitor
emite UM voto por cargo. O controle de duplicidade é feito pela tabela
`participacao` (eleicao_id, eleitor_id, cargo) com restrição UNIQUE — é ela,
e não uma flag, que garante "um voto por cargo" mesmo sob concorrência.

A tabela `votos` não tem qualquer referência ao eleitor: o sigilo é
preservado mesmo com a participação registrada.
"""

from database import (
    db_session, registrar_auditoria, logger, fazer_backup,
)
from datetime import datetime
import csv
import json
import os
import re
import sqlite3


class ErroApuracao(Exception):
    """Levantada quando a apuração não pode ser concluída com integridade."""

    def __init__(self, mensagem: str, detalhes: list | None = None):
        super().__init__(mensagem)
        self.detalhes = detalhes or []


# ==================== UTILITÁRIOS CPF ====================

def normalizar_cpf(cpf: str) -> str:
    """Remove pontuação e espaços do CPF, deixando só dígitos."""
    return re.sub(r"\D", "", (cpf or "").strip())


def validar_cpf(cpf: str) -> bool:
    """
    Valida CPF pelo algoritmo oficial (dígitos verificadores, módulo 11).
    Rejeita sequências repetidas (111.111.111-11 etc.).
    """
    cpf = normalizar_cpf(cpf)
    if len(cpf) != 11:
        return False
    if cpf == cpf[0] * 11:
        return False

    # Primeiro dígito verificador
    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    resto = soma % 11
    digito1 = 0 if resto < 2 else 11 - resto
    if int(cpf[9]) != digito1:
        return False

    # Segundo dígito verificador
    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    resto = soma % 11
    digito2 = 0 if resto < 2 else 11 - resto
    if int(cpf[10]) != digito2:
        return False

    return True


def gerar_cpf_valido(base9: str) -> str:
    """Gera CPF completo (11 dígitos) a partir de 9 dígitos base, com DV corretos."""
    base9 = normalizar_cpf(base9)[:9].ljust(9, "0")
    soma = sum(int(base9[i]) * (10 - i) for i in range(9))
    resto = soma % 11
    d1 = 0 if resto < 2 else 11 - resto
    base10 = base9 + str(d1)
    soma = sum(int(base10[i]) * (11 - i) for i in range(10))
    resto = soma % 11
    d2 = 0 if resto < 2 else 11 - resto
    return base10 + str(d2)


def formatar_cpf(cpf: str) -> str:
    """Formata CPF como 000.000.000-00."""
    cpf = normalizar_cpf(cpf)
    if len(cpf) != 11:
        return cpf
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def mascarar_cpf(cpf: str) -> str:
    """Mascara o CPF para exibição a quem não é administrador: 123.***.***-09."""
    cpf = normalizar_cpf(cpf)
    if len(cpf) != 11:
        return "***"
    return f"{cpf[:3]}.***.***-{cpf[9:]}"


# ==================== ELEIÇÕES ====================

def criar_eleicao(nome: str, descricao: str, tipo: str, voto_ponderado: bool = False) -> int:
    """Cria uma nova eleição. Retorna o ID."""
    tipos_validos = ("condominio", "associacao", "sindicato")
    if tipo not in tipos_validos:
        raise ValueError(f"Tipo inválido. Use: {', '.join(tipos_validos)}")
    if not (nome or "").strip():
        raise ValueError("O nome da eleição é obrigatório.")

    with db_session(escrita=True) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO eleicoes (nome, descricao, tipo, voto_ponderado)
               VALUES (?, ?, ?, ?)""",
            (nome.strip(), descricao, tipo, 1 if voto_ponderado else 0)
        )
        eleicao_id = cursor.lastrowid
        registrar_auditoria(
            conn, "CRIAR_ELEICAO",
            f"ID={eleicao_id} Nome={nome} Tipo={tipo} Ponderado={voto_ponderado}"
        )
        return eleicao_id


def listar_eleicoes(status: str | None = None) -> list[dict]:
    """Lista eleições, opcionalmente filtradas por status."""
    with db_session() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                "SELECT * FROM eleicoes WHERE status = ? ORDER BY id DESC",
                (status,)
            )
        else:
            cursor.execute("SELECT * FROM eleicoes ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]


def obter_eleicao(eleicao_id: int) -> dict | None:
    """Retorna dados de uma eleição pelo ID."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM eleicoes WHERE id = ?", (eleicao_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def atualizar_eleicao(
    eleicao_id: int,
    nome: str | None = None,
    descricao: str | None = None,
    voto_ponderado: bool | None = None,
    autor: str = "sistema",
) -> bool:
    """
    Edita dados de uma eleição. O modo ponderado só pode mudar enquanto
    não houver votos registrados (mudá-lo depois falsearia a apuração).
    """
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    campos, valores = [], []
    if nome is not None:
        if not nome.strip():
            raise ValueError("O nome não pode ficar vazio.")
        campos.append("nome = ?")
        valores.append(nome.strip())
    if descricao is not None:
        campos.append("descricao = ?")
        valores.append(descricao)
    if voto_ponderado is not None and bool(voto_ponderado) != bool(eleicao["voto_ponderado"]):
        if contar_votos(eleicao_id) > 0:
            raise ValueError("Não é possível mudar o modo ponderado com votos já registrados.")
        campos.append("voto_ponderado = ?")
        valores.append(1 if voto_ponderado else 0)

    if not campos:
        return False

    valores.append(eleicao_id)
    with db_session(escrita=True) as conn:
        conn.execute(f"UPDATE eleicoes SET {', '.join(campos)} WHERE id = ?", valores)
        registrar_auditoria(
            conn, "EDITAR_ELEICAO", f"ID={eleicao_id} Campos={', '.join(campos)}", autor
        )
    return True


def excluir_eleicao(eleicao_id: int, autor: str = "sistema") -> bool:
    """
    Exclui uma eleição e tudo que depende dela.
    Recusa se já houver votos — nesse caso a eleição é histórico e deve
    permanecer. Faz backup antes de apagar.
    """
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")
    if contar_votos(eleicao_id) > 0:
        raise ValueError(
            "Eleição com votos registrados não pode ser excluída (preservação do histórico)."
        )

    try:
        fazer_backup("antes_excluir_eleicao")
    except OSError:
        pass

    with db_session(escrita=True) as conn:
        registrar_auditoria(
            conn, "EXCLUIR_ELEICAO", f"ID={eleicao_id} Nome={eleicao['nome']}", autor
        )
        conn.execute("DELETE FROM eleicoes WHERE id = ?", (eleicao_id,))
    logger.warning("Eleição %s excluída por %s", eleicao_id, autor)
    return True


def contar_votos(eleicao_id: int) -> int:
    with db_session() as conn:
        return conn.execute(
            "SELECT COUNT(*) as t FROM votos WHERE eleicao_id = ?", (eleicao_id,)
        ).fetchone()["t"]


def alterar_status_eleicao(eleicao_id: int, novo_status: str, autor: str = "sistema") -> bool:
    """Altera o status de uma eleição (preparacao / aberta / fechada)."""
    status_validos = ("preparacao", "aberta", "fechada")
    if novo_status not in status_validos:
        raise ValueError(f"Status inválido. Use: {', '.join(status_validos)}")

    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        return False

    if novo_status == "preparacao" and contar_votos(eleicao_id) > 0:
        raise ValueError(
            "Não é possível voltar para preparação: já existem votos registrados."
        )

    # Os contadores homomórficos são criados UMA vez. Reabrir uma eleição
    # não pode zerar a soma cifrada dos votos já depositados.
    contadores_json = None
    if novo_status == "aberta" and not eleicao.get("contadores_homo"):
        try:
            contadores_json = _iniciar_contadores_homo(eleicao_id)
        except Exception as e:
            logger.error("Falha ao iniciar contadores homomórficos: %s", e)
            contadores_json = None

    with db_session(escrita=True) as conn:
        cursor = conn.cursor()
        agora = datetime.now().isoformat(timespec="seconds")

        if novo_status == "aberta":
            if contadores_json is not None:
                cursor.execute(
                    "UPDATE eleicoes SET status = ?, data_abertura = ?, contadores_homo = ? WHERE id = ?",
                    (novo_status, agora, contadores_json, eleicao_id)
                )
            else:
                cursor.execute(
                    "UPDATE eleicoes SET status = ?, data_abertura = COALESCE(data_abertura, ?) WHERE id = ?",
                    (novo_status, agora, eleicao_id)
                )
        elif novo_status == "fechada":
            cursor.execute(
                "UPDATE eleicoes SET status = ?, data_fechamento = ? WHERE id = ?",
                (novo_status, agora, eleicao_id)
            )
        else:
            cursor.execute(
                "UPDATE eleicoes SET status = ? WHERE id = ?",
                (novo_status, eleicao_id)
            )

        registrar_auditoria(
            conn, "ALTERAR_STATUS",
            f"Eleição {eleicao_id}: {eleicao['status']} → {novo_status}",
            autor,
        )
    logger.info("Eleição %s: %s → %s", eleicao_id, eleicao["status"], novo_status)
    return True


def _chave_homo(tipo_voto: str, candidato_id: int | None, cargo: str) -> str:
    """Nome do contador homomórfico para uma escolha."""
    if tipo_voto == "candidato" and candidato_id:
        return f"cand:{candidato_id}"
    return f"{tipo_voto}:{cargo}"


def _iniciar_contadores_homo(eleicao_id: int) -> str:
    """Cria contadores Enc(0) por candidato e por (branco|nulo) × cargo."""
    from homo_voto import carregar_ou_gerar, iniciar_contadores
    pub, _priv = carregar_ou_gerar()
    cands = listar_candidatos(eleicao_id)
    chaves = [f"cand:{c['id']}" for c in cands]
    for cargo in listar_cargos(eleicao_id):
        chaves.append(f"branco:{cargo}")
        chaves.append(f"nulo:{cargo}")
    cont = iniciar_contadores(pub, chaves)
    return json.dumps(cont)


def criar_eleicao_demonstracao() -> int:
    """Cria uma eleição completa de demonstração com candidatos e eleitores."""
    eid = criar_eleicao(
        nome="Eleição do Síndico 2026 (Demonstração)",
        descricao="Eleição demonstrativa do condomínio Residencial Exemplo",
        tipo="condominio",
        voto_ponderado=True
    )

    # Candidatos — dois cargos, o eleitor votará uma vez em cada
    cadastrar_candidato(eid, 10, "João Silva", "Síndico", "Proposta: melhorias na segurança e área de lazer")
    cadastrar_candidato(eid, 20, "Maria Santos", "Síndico", "Proposta: transparência nas contas e redução de taxas")
    cadastrar_candidato(eid, 30, "Pedro Oliveira", "Conselheiro", "Experiência em administração condominial")
    cadastrar_candidato(eid, 40, "Ana Costa", "Conselheiro", "Foco em sustentabilidade e economia de energia")

    # Eleitores com pesos diferentes (fração ideal)
    # CPFs válidos pelo algoritmo oficial (dígitos verificadores)
    eleitores_demo = [
        ("Carlos Mendes", "123.456.789-09", "101", "A", 1.0),
        ("Fernanda Lima", "234.567.890-92", "202", "A", 1.5),
        ("Roberto Alves", "345.678.901-75", "301", "B", 1.0),
        ("Juliana Rocha", "456.789.012-49", "102", "A", 2.0),
        ("Marcos Pereira", "567.890.123-03", "203", "B", 1.0),
        ("Patrícia Souza", "678.901.234-69", "401", "C", 1.25),
    ]
    for nome, cpf, un, bl, peso in eleitores_demo:
        cadastrar_eleitor(eid, nome, cpf, unidade=un, bloco=bl, peso=peso)

    return eid


# ==================== CANDIDATOS ====================

def cadastrar_candidato(eleicao_id: int, numero: int, nome: str, cargo: str, descricao: str = "") -> int:
    """Cadastra um candidato em uma eleição."""
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")
    if eleicao["status"] != "preparacao":
        raise ValueError("Só é possível cadastrar candidatos em eleições em preparação.")
    if not (nome or "").strip() or not (cargo or "").strip():
        raise ValueError("Nome e cargo são obrigatórios.")
    if int(numero) <= 0:
        raise ValueError("O número do candidato deve ser positivo.")

    with db_session(escrita=True) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO candidatos (eleicao_id, numero, nome, cargo, descricao)
                   VALUES (?, ?, ?, ?, ?)""",
                (eleicao_id, int(numero), nome.strip(), cargo.strip(), descricao)
            )
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e):
                raise ValueError(f"Já existe candidato com o número {numero} nesta eleição.")
            raise
        candidato_id = cursor.lastrowid
        registrar_auditoria(
            conn, "CADASTRAR_CANDIDATO",
            f"Eleição={eleicao_id} Número={numero} Nome={nome} Cargo={cargo}"
        )
        return candidato_id


def obter_candidato(candidato_id: int) -> dict | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM candidatos WHERE id = ?", (candidato_id,)).fetchone()
        return dict(row) if row else None


def atualizar_candidato(
    candidato_id: int,
    numero: int | None = None,
    nome: str | None = None,
    cargo: str | None = None,
    descricao: str | None = None,
    autor: str = "sistema",
) -> bool:
    """Edita um candidato. Só permitido enquanto a eleição está em preparação."""
    cand = obter_candidato(candidato_id)
    if not cand:
        raise ValueError("Candidato não encontrado.")
    eleicao = obter_eleicao(cand["eleicao_id"])
    if eleicao["status"] != "preparacao":
        raise ValueError("Só é possível editar candidatos com a eleição em preparação.")

    campos, valores = [], []
    if numero is not None:
        campos.append("numero = ?")
        valores.append(int(numero))
    if nome is not None and nome.strip():
        campos.append("nome = ?")
        valores.append(nome.strip())
    if cargo is not None and cargo.strip():
        campos.append("cargo = ?")
        valores.append(cargo.strip())
    if descricao is not None:
        campos.append("descricao = ?")
        valores.append(descricao)
    if not campos:
        return False

    valores.append(candidato_id)
    with db_session(escrita=True) as conn:
        try:
            conn.execute(f"UPDATE candidatos SET {', '.join(campos)} WHERE id = ?", valores)
        except sqlite3.IntegrityError:
            raise ValueError(f"Já existe outro candidato com o número {numero} nesta eleição.")
        registrar_auditoria(
            conn, "EDITAR_CANDIDATO", f"ID={candidato_id} Campos={', '.join(campos)}", autor
        )
    return True


def excluir_candidato(candidato_id: int, autor: str = "sistema") -> bool:
    """
    Remove um candidato em preparação. Se a eleição já teve votos, o
    candidato é apenas desativado para não quebrar a apuração histórica.
    """
    cand = obter_candidato(candidato_id)
    if not cand:
        raise ValueError("Candidato não encontrado.")
    eleicao = obter_eleicao(cand["eleicao_id"])
    tem_votos = contar_votos(cand["eleicao_id"]) > 0

    with db_session(escrita=True) as conn:
        if eleicao["status"] == "preparacao" and not tem_votos:
            conn.execute("DELETE FROM candidatos WHERE id = ?", (candidato_id,))
            acao = "EXCLUIR_CANDIDATO"
        else:
            conn.execute("UPDATE candidatos SET ativo = 0 WHERE id = ?", (candidato_id,))
            acao = "DESATIVAR_CANDIDATO"
        registrar_auditoria(
            conn, acao, f"ID={candidato_id} Nome={cand['nome']} Cargo={cand['cargo']}", autor
        )
    return True


def listar_candidatos(eleicao_id: int, apenas_ativos: bool = True, cargo: str | None = None) -> list[dict]:
    """Lista candidatos de uma eleição, opcionalmente de um único cargo."""
    sql = "SELECT * FROM candidatos WHERE eleicao_id = ?"
    params: list = [eleicao_id]
    if apenas_ativos:
        sql += " AND ativo = 1"
    if cargo:
        sql += " AND cargo = ?"
        params.append(cargo)
    sql += " ORDER BY cargo, numero"
    with db_session() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def obter_candidato_por_numero(eleicao_id: int, numero: int, cargo: str | None = None) -> dict | None:
    """Busca candidato pelo número na eleição (opcionalmente restrito a um cargo)."""
    sql = "SELECT * FROM candidatos WHERE eleicao_id = ? AND numero = ? AND ativo = 1"
    params: list = [eleicao_id, numero]
    if cargo:
        sql += " AND cargo = ?"
        params.append(cargo)
    with db_session() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def listar_cargos(eleicao_id: int) -> list[str]:
    """Retorna lista de cargos distintos da eleição."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT DISTINCT cargo FROM candidatos
               WHERE eleicao_id = ? AND ativo = 1 ORDER BY cargo""",
            (eleicao_id,)
        ).fetchall()
        return [row["cargo"] for row in rows]


# ==================== ELEITORES ====================

def cadastrar_eleitor(
    eleicao_id: int,
    nome: str,
    documento: str,
    codigo_acesso: str = "",
    unidade: str = "",
    bloco: str = "",
    peso: float = 1.0
) -> int:
    """
    Cadastra um eleitor em uma eleição.
    O documento deve ser o CPF (com ou sem pontuação).
    O controle de quem já votou é feito pelo CPF.
    """
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")
    if eleicao["status"] == "fechada":
        raise ValueError("Não é possível cadastrar eleitores em eleição fechada.")

    if not (nome or "").strip():
        raise ValueError("O nome do eleitor é obrigatório.")
    if peso <= 0:
        raise ValueError("O peso do voto deve ser maior que zero.")

    cpf = normalizar_cpf(documento)
    if not validar_cpf(cpf):
        raise ValueError("CPF inválido. Informe 11 dígitos (ex: 123.456.789-00).")

    # Se não informar código, usa o próprio CPF (identificação única)
    codigo = (codigo_acesso or "").strip() or cpf

    with db_session(escrita=True) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO eleitores
                   (eleicao_id, nome, documento, unidade, bloco, codigo_acesso, peso)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (eleicao_id, nome.strip(), cpf, unidade, bloco, codigo, peso)
            )
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e):
                raise ValueError("Este CPF já está cadastrado nesta eleição.")
            raise
        eleitor_id = cursor.lastrowid
        registrar_auditoria(
            conn, "CADASTRAR_ELEITOR",
            f"Eleição={eleicao_id} Nome={nome} CPF={formatar_cpf(cpf)} Peso={peso}"
        )
        return eleitor_id


def obter_eleitor(eleitor_id: int) -> dict | None:
    with db_session() as conn:
        row = conn.execute("SELECT * FROM eleitores WHERE id = ?", (eleitor_id,)).fetchone()
        return dict(row) if row else None


def atualizar_eleitor(
    eleitor_id: int,
    nome: str | None = None,
    documento: str | None = None,
    unidade: str | None = None,
    bloco: str | None = None,
    peso: float | None = None,
    autor: str = "sistema",
) -> bool:
    """
    Edita um eleitor. CPF e peso ficam travados depois que a pessoa começou
    a votar — mudá-los alteraria retroativamente o valor de votos já dados.
    """
    eleitor = obter_eleitor(eleitor_id)
    if not eleitor:
        raise ValueError("Eleitor não encontrado.")
    ja_participou = len(cargos_votados(eleitor["eleicao_id"], eleitor_id)) > 0

    campos, valores = [], []
    if nome is not None and nome.strip():
        campos.append("nome = ?")
        valores.append(nome.strip())
    if documento is not None:
        cpf = normalizar_cpf(documento)
        if cpf != eleitor["documento"]:
            if ja_participou:
                raise ValueError("Não é possível trocar o CPF de quem já votou.")
            if not validar_cpf(cpf):
                raise ValueError("CPF inválido.")
            campos.append("documento = ?")
            valores.append(cpf)
            campos.append("codigo_acesso = ?")
            valores.append(cpf)
    if unidade is not None:
        campos.append("unidade = ?")
        valores.append(unidade)
    if bloco is not None:
        campos.append("bloco = ?")
        valores.append(bloco)
    if peso is not None and float(peso) != float(eleitor["peso"]):
        if ja_participou:
            raise ValueError("Não é possível alterar o peso de quem já votou.")
        if float(peso) <= 0:
            raise ValueError("O peso deve ser maior que zero.")
        campos.append("peso = ?")
        valores.append(float(peso))

    if not campos:
        return False

    valores.append(eleitor_id)
    with db_session(escrita=True) as conn:
        try:
            conn.execute(f"UPDATE eleitores SET {', '.join(campos)} WHERE id = ?", valores)
        except sqlite3.IntegrityError:
            raise ValueError("Este CPF já está cadastrado nesta eleição.")
        registrar_auditoria(
            conn, "EDITAR_ELEITOR", f"ID={eleitor_id} Campos={', '.join(campos)}", autor
        )
    return True


def excluir_eleitor(eleitor_id: int, autor: str = "sistema") -> bool:
    """
    Remove um eleitor que ainda não votou. Quem já votou é desativado,
    nunca apagado — senão o total de aptos deixaria de bater com a urna.
    """
    eleitor = obter_eleitor(eleitor_id)
    if not eleitor:
        raise ValueError("Eleitor não encontrado.")

    if cargos_votados(eleitor["eleicao_id"], eleitor_id):
        raise ValueError(
            "Este eleitor já votou e não pode ser removido (a urna deixaria de bater). "
            "Use a desativação apenas se orientado pela comissão eleitoral."
        )

    with db_session(escrita=True) as conn:
        conn.execute("DELETE FROM eleitores WHERE id = ?", (eleitor_id,))
        registrar_auditoria(
            conn, "EXCLUIR_ELEITOR",
            f"ID={eleitor_id} Nome={eleitor['nome']} CPF={formatar_cpf(eleitor['documento'])}",
            autor,
        )
    return True


def listar_eleitores(eleicao_id: int, mascarar: bool = False) -> list[dict]:
    """Lista eleitores de uma eleição, com quantos cargos cada um já votou."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT e.id, e.nome, e.documento, e.unidade, e.bloco, e.peso,
                      e.ja_votou, e.data_voto, e.ativo,
                      (SELECT COUNT(*) FROM participacao p
                        WHERE p.eleitor_id = e.id) AS cargos_votados
               FROM eleitores e WHERE e.eleicao_id = ? ORDER BY e.nome""",
            (eleicao_id,)
        ).fetchall()

    total_cargos = len(listar_cargos(eleicao_id))
    saida = []
    for row in rows:
        r = dict(row)
        r["total_cargos"] = total_cargos
        r["cpf_formatado"] = formatar_cpf(r["documento"])
        r["cpf_mascarado"] = mascarar_cpf(r["documento"])
        if mascarar:
            r["documento"] = r["cpf_mascarado"]
            r["cpf_formatado"] = r["cpf_mascarado"]
        saida.append(r)
    return saida


def autenticar_eleitor(eleicao_id: int, cpf: str) -> dict | None:
    """
    Autentica eleitor pelo CPF.
    Aceita CPF com ou sem pontuação e exige que ele seja válido.
    Retorna também os cargos que faltam votar.
    """
    cpf_norm = normalizar_cpf(cpf)
    if not validar_cpf(cpf_norm):
        return None

    with db_session() as conn:
        row = conn.execute(
            """SELECT * FROM eleitores
               WHERE eleicao_id = ? AND documento = ? AND ativo = 1""",
            (eleicao_id, cpf_norm)
        ).fetchone()
    if not row:
        return None

    eleitor = dict(row)
    eleitor["cargos_pendentes"] = cargos_pendentes(eleicao_id, eleitor["id"])
    eleitor["cargos_votados"] = cargos_votados(eleicao_id, eleitor["id"])
    return eleitor


def cargos_votados(eleicao_id: int, eleitor_id: int) -> list[str]:
    """Cargos em que este eleitor já depositou voto."""
    with db_session() as conn:
        rows = conn.execute(
            "SELECT cargo FROM participacao WHERE eleicao_id = ? AND eleitor_id = ? ORDER BY cargo",
            (eleicao_id, eleitor_id),
        ).fetchall()
    return [r["cargo"] for r in rows]


def cargos_pendentes(eleicao_id: int, eleitor_id: int) -> list[str]:
    """Cargos que este eleitor ainda precisa votar."""
    votados = set(cargos_votados(eleicao_id, eleitor_id))
    return [c for c in listar_cargos(eleicao_id) if c not in votados]


def marcar_como_votou(eleitor_id: int):
    """
    Mantido por compatibilidade. A marcação agora é feita dentro da mesma
    transação de registrar_voto, quando o eleitor completa todos os cargos.
    """
    eleitor = obter_eleitor(eleitor_id)
    if not eleitor:
        return
    _atualizar_conclusao(eleitor["eleicao_id"], eleitor_id)


def _atualizar_conclusao(eleicao_id: int, eleitor_id: int, conn=None):
    """Marca ja_votou=1 quando o eleitor votou em todos os cargos da eleição."""
    def _executar(c):
        total_cargos = c.execute(
            "SELECT COUNT(DISTINCT cargo) as t FROM candidatos WHERE eleicao_id = ? AND ativo = 1",
            (eleicao_id,),
        ).fetchone()["t"]
        votados = c.execute(
            "SELECT COUNT(*) as t FROM participacao WHERE eleicao_id = ? AND eleitor_id = ?",
            (eleicao_id, eleitor_id),
        ).fetchone()["t"]
        if total_cargos > 0 and votados >= total_cargos:
            c.execute(
                "UPDATE eleitores SET ja_votou = 1, data_voto = COALESCE(data_voto, ?) WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), eleitor_id),
            )

    if conn is not None:
        _executar(conn)
    else:
        with db_session(escrita=True) as c:
            _executar(c)


# ==================== VOTAÇÃO ====================

def registrar_voto(
    eleicao_id: int,
    eleitor_id: int,
    cargo: str,
    tipo_voto: str,
    candidato_id: int | None = None,
    peso: float | None = None,
) -> int:
    """
    Registra o voto de um eleitor em UM cargo, de forma atômica.

    Garantias:
      - A inserção em `participacao` (UNIQUE eleicao+eleitor+cargo) e a
        inserção em `votos` acontecem na MESMA transação BEGIN IMMEDIATE.
        Duas requisições simultâneas do mesmo eleitor: uma grava, a outra
        recebe "já votou neste cargo". Não há janela para voto duplo.
      - O voto gravado não tem nenhuma referência ao eleitor.
      - Voto nominal carrega prova OR de Schnorr ("é um dos candidatos
        oficiais deste cargo"), verificada antes de gravar.
    """
    if tipo_voto not in ("candidato", "branco", "nulo"):
        raise ValueError("Tipo de voto inválido.")

    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")
    if eleicao["status"] != "aberta":
        raise ValueError("A votação não está aberta.")

    cargo = (cargo or "").strip()
    cargos = listar_cargos(eleicao_id)
    if cargo not in cargos:
        raise ValueError(f"Cargo inválido para esta eleição: {cargo!r}")

    eleitor = obter_eleitor(eleitor_id)
    if not eleitor or eleitor["eleicao_id"] != eleicao_id or not eleitor["ativo"]:
        raise ValueError("Eleitor inválido para esta eleição.")

    if peso is None:
        peso = float(eleitor["peso"] or 1.0)
    peso = float(peso)

    # Candidatos oficiais DESTE cargo
    cands_cargo = listar_candidatos(eleicao_id, cargo=cargo)
    ids_cargo = [c["id"] for c in cands_cargo]

    if tipo_voto == "candidato":
        if not candidato_id:
            raise ValueError("Candidato é obrigatório para voto nominal.")
        if candidato_id not in ids_cargo:
            raise ValueError("Candidato não pertence a este cargo nesta eleição.")
    else:
        candidato_id = None

    # Criptografia fora da transação: é a parte cara e não precisa do lock
    from crypto_voto import cifrar_voto
    pacote = cifrar_voto(tipo_voto, candidato_id, peso, cargo=cargo)

    prova_or_json = None
    if tipo_voto == "candidato":
        from schnorr_or import provar_voto_valido, verificar_prova_voto_valido, prova_para_json
        pacote_or = provar_voto_valido(candidato_id, ids_cargo, eleicao_id)
        if not verificar_prova_voto_valido(pacote_or):
            raise ValueError("Falha interna: prova OR inválida após geração.")
        prova_or_json = prova_para_json(pacote_or)

    with db_session(escrita=True) as conn:
        cursor = conn.cursor()

        # Revalida o estado da eleição já dentro da transação
        estado = cursor.execute(
            "SELECT status FROM eleicoes WHERE id = ?", (eleicao_id,)
        ).fetchone()
        if not estado or estado["status"] != "aberta":
            raise ValueError("A votação foi fechada.")

        # É esta linha que impede o voto duplo, inclusive sob concorrência
        try:
            cursor.execute(
                "INSERT INTO participacao (eleicao_id, eleitor_id, cargo) VALUES (?, ?, ?)",
                (eleicao_id, eleitor_id, cargo),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Este eleitor já votou para o cargo de {cargo}.")

        cursor.execute(
            """INSERT INTO votos
               (eleicao_id, candidato_id, tipo_voto, cargo, peso,
                voto_cifrado, integridade, compromisso, prova_or)
               VALUES (?, NULL, NULL, ?, ?, ?, ?, ?, ?)""",
            (
                eleicao_id,
                cargo,
                peso,
                pacote["voto_cifrado"],
                pacote["integridade"],
                pacote["compromisso"],
                prova_or_json,
            )
        )
        voto_id = cursor.lastrowid

        # Acumula no contador homomórfico (sem abrir totais)
        try:
            _acumular_homo(cursor, eleicao_id, tipo_voto, candidato_id, cargo, peso)
        except Exception as e:
            logger.error("Falha ao acumular contador homomórfico: %s", e)

        _atualizar_conclusao(eleicao_id, eleitor_id, cursor)

        registrar_auditoria(
            conn, "VOTO_REGISTRADO",
            f"Eleição={eleicao_id} Cargo={cargo} Peso={peso} Cifrado=sim "
            f"OR={'sim' if prova_or_json else 'nao'} "
            f"Compromisso={pacote['compromisso'][:16]}…"
        )
        return voto_id


def _acumular_homo(cursor, eleicao_id: int, tipo_voto: str, candidato_id: int | None,
                   cargo: str, peso: float):
    """Soma Enc(peso) no contador da escolha (Paillier)."""
    from homo_voto import carregar_ou_gerar, acumular_voto

    row = cursor.execute(
        "SELECT contadores_homo FROM eleicoes WHERE id = ?", (eleicao_id,)
    ).fetchone()
    if not row or not row["contadores_homo"]:
        return
    cont = json.loads(row["contadores_homo"])
    pub, _ = carregar_ou_gerar()

    chave = _chave_homo(tipo_voto, candidato_id, cargo)
    if chave not in cont:
        # Cargo/candidato criado depois da abertura: cria o contador agora
        cont[chave] = str(pub.encrypt(0))

    # peso como inteiro (centésimos, para suportar frações ideais)
    peso_int = int(round(float(peso) * 100))
    cont = acumular_voto(pub, cont, chave, peso_int)
    cursor.execute(
        "UPDATE eleicoes SET contadores_homo = ? WHERE id = ?",
        (json.dumps(cont), eleicao_id),
    )


def obter_resultados_homo(eleicao_id: int) -> dict:
    """
    Apuração via criptografia homomórfica:
    decifra apenas os totais acumulados (não cada voto).
    Pesos foram armazenados em centésimos.
    """
    from homo_voto import carregar_ou_gerar, decifrar_contadores

    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")
    if not eleicao.get("contadores_homo"):
        raise ValueError("Contadores homomórficos não inicializados (abra a eleição).")

    _pub, priv = carregar_ou_gerar()
    cont = json.loads(eleicao["contadores_homo"])
    totais_raw = decifrar_contadores(priv, cont)

    cands = listar_candidatos(eleicao_id)
    por_cargo: dict[str, dict] = {}
    for c in cands:
        peso_cent = totais_raw.get(f"cand:{c['id']}", 0)
        entrada = por_cargo.setdefault(
            c["cargo"], {"candidatos": [], "brancos_peso": 0.0, "nulos_peso": 0.0}
        )
        entrada["candidatos"].append({
            "id": c["id"],
            "numero": c["numero"],
            "nome": c["nome"],
            "cargo": c["cargo"],
            "peso_votos": peso_cent / 100.0,
        })

    for cargo, entrada in por_cargo.items():
        entrada["candidatos"].sort(key=lambda x: (-x["peso_votos"], x["numero"]))
        entrada["brancos_peso"] = totais_raw.get(f"branco:{cargo}", 0) / 100.0
        entrada["nulos_peso"] = totais_raw.get(f"nulo:{cargo}", 0) / 100.0

    return {
        "eleicao_id": eleicao_id,
        "esquema": "Paillier aditivo",
        "por_cargo": por_cargo,
        "totais_brutos_centesimos": totais_raw,
    }


def obter_recibo_voto(voto_id: int) -> dict | None:
    """Recibo público do voto (compromisso + se tem prova OR)."""
    with db_session() as conn:
        row = conn.execute(
            """SELECT id, eleicao_id, cargo, compromisso, prova_or, registrado_em
               FROM votos WHERE id = ?""",
            (voto_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["tem_prova_or"] = bool(d.get("prova_or"))
        d.pop("prova_or", None)
        return d


def verificar_recibo(eleicao_id: int, compromisso: str) -> dict:
    """
    Confere se um recibo (compromisso) está no quadro público da eleição e
    devolve a prova de inclusão Merkle. Não revela o conteúdo do voto.
    """
    from zk_voto import prova_merkle, verificar_prova_merkle, construir_merkle

    compromisso = (compromisso or "").strip().lower()
    quadro = quadro_compromissos(eleicao_id)
    lista = [c["compromisso"] for c in quadro["compromissos"]]

    if compromisso not in lista:
        return {
            "encontrado": False,
            "eleicao_id": eleicao_id,
            "merkle_raiz": quadro["merkle_raiz"],
            "total_no_quadro": quadro["total"],
            "mensagem": "Este recibo NÃO consta no quadro público desta eleição.",
        }

    indice = lista.index(compromisso)
    prova = prova_merkle(lista, indice)
    valido = verificar_prova_merkle(compromisso, prova) if prova else False
    registro = quadro["compromissos"][indice]

    return {
        "encontrado": True,
        "eleicao_id": eleicao_id,
        "indice": indice,
        "cargo": registro.get("cargo"),
        "registrado_em": registro.get("registrado_em"),
        "merkle_raiz": construir_merkle(lista)["raiz"],
        "prova_merkle": prova,
        "prova_valida": valido,
        "total_no_quadro": quadro["total"],
        "mensagem": "Recibo encontrado e prova de inclusão válida."
                    if valido else "Recibo encontrado, mas a prova de inclusão falhou!",
    }


def verificar_provas_or(eleicao_id: int) -> dict:
    """
    Verifica em Python todas as provas OR de Schnorr da eleição.
    Não revela em quem cada um votou — só se a prova é válida.
    """
    from schnorr_or import verificar_prova_voto_valido

    with db_session() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT id, cargo, prova_or, compromisso, registrado_em
               FROM votos WHERE eleicao_id = ? ORDER BY id""",
            (eleicao_id,),
        ).fetchall()]

    total = len(rows)
    com_prova = 0
    validas = 0
    invalidas = []
    sem_prova = []  # branco/nulo ou legado

    for r in rows:
        if not r.get("prova_or"):
            sem_prova.append(r["id"])
            continue
        com_prova += 1
        try:
            pacote = _json_ints(json.loads(r["prova_or"]))
            if verificar_prova_voto_valido(pacote):
                validas += 1
            else:
                invalidas.append({"voto_id": r["id"], "motivo": "verificacao_falhou"})
        except Exception as e:
            invalidas.append({"voto_id": r["id"], "motivo": str(e)})

    return {
        "eleicao_id": eleicao_id,
        "total_votos": total,
        "com_prova_or": com_prova,
        "provas_validas": validas,
        "provas_invalidas": invalidas,
        "sem_prova_or": sem_prova,
        "ok": len(invalidas) == 0 and (com_prova == 0 or validas == com_prova),
    }


def _json_ints(obj):
    """Converte strings numéricas de volta para int (provas serializadas)."""
    if isinstance(obj, dict):
        return {k: _json_ints(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_ints(x) for x in obj]
    if isinstance(obj, str) and obj.isdigit():
        return int(obj)
    return obj


def quadro_compromissos(eleicao_id: int) -> dict:
    """Quadro público de compromissos + raiz Merkle (não revela conteúdo dos votos)."""
    from zk_voto import construir_merkle

    with db_session() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT id, cargo, compromisso, registrado_em
               FROM votos
               WHERE eleicao_id = ? AND compromisso IS NOT NULL
               ORDER BY id""",
            (eleicao_id,),
        ).fetchall()]

    compromissos = [r["compromisso"] for r in rows]
    merkle = construir_merkle(compromissos)
    return {
        "eleicao_id": eleicao_id,
        "total": len(compromissos),
        "compromissos": [
            {
                "voto_id": r["id"],
                "cargo": r["cargo"],
                "compromisso": r["compromisso"],
                "registrado_em": r["registrado_em"],
            }
            for r in rows
        ],
        "merkle_raiz": merkle["raiz"],
    }


def exportar_quadro_compromissos(eleicao_id: int, caminho: str | None = None) -> str:
    """
    Exporta o quadro público (compromissos + raiz Merkle) em CSV, para
    publicação/afixação. Não contém nenhum dado pessoal nem o conteúdo dos votos.
    """
    quadro = quadro_compromissos(eleicao_id)
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    if not caminho:
        caminho = _caminho_saida(f"quadro_compromissos_{_nome_seguro(eleicao['nome'])}_{eleicao_id}.csv")

    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["QUADRO PÚBLICO DE COMPROMISSOS"])
        w.writerow(["Eleição", eleicao["nome"]])
        w.writerow(["Status", eleicao["status"]])
        w.writerow(["Total de compromissos", quadro["total"]])
        w.writerow(["Raiz Merkle", quadro["merkle_raiz"]])
        w.writerow(["Gerado em", datetime.now().isoformat(timespec="seconds")])
        w.writerow([])
        w.writerow(["ORDEM", "CARGO", "COMPROMISSO (SHA-256)", "REGISTRADO EM"])
        for i, c in enumerate(quadro["compromissos"]):
            w.writerow([i, c["cargo"], c["compromisso"], c["registrado_em"]])
    return caminho


def verificar_votos_zk(eleicao_id: int) -> dict:
    """
    Verificação estilo ZK: confere compromissos, validade dos candidatos e Merkle.
    Usa a chave para abrir os votos (autoridade de apuração).
    """
    from zk_voto import verificar_apuracao, construir_merkle
    from crypto_voto import decifrar_voto

    with db_session() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT id, voto_cifrado, integridade, compromisso, candidato_id,
                      tipo_voto, cargo, peso
               FROM votos WHERE eleicao_id = ? ORDER BY id""",
            (eleicao_id,),
        ).fetchall()]
        candidatos_validos = {
            r["id"] for r in conn.execute(
                "SELECT id FROM candidatos WHERE eleicao_id = ?", (eleicao_id,)
            ).fetchall()
        }

    aberturas = []
    compromissos = []
    falhas_decifra = []
    for r in rows:
        if r.get("voto_cifrado") and r.get("compromisso"):
            try:
                ab = decifrar_voto(r["voto_cifrado"], r.get("integridade"))
                aberturas.append(ab)
            except ValueError as e:
                falhas_decifra.append({"voto_id": r["id"], "erro": str(e)})
                aberturas.append({"tipo": "erro", "candidato_id": None,
                                  "cargo": r.get("cargo") or "", "peso": 1.0, "nonce": ""})
            compromissos.append(r["compromisso"])
        else:
            aberturas.append({
                "tipo": r.get("tipo_voto") or "nulo",
                "candidato_id": r.get("candidato_id"),
                "cargo": r.get("cargo") or "",
                "peso": float(r.get("peso") or 1),
                "nonce": "",
            })
            compromissos.append(r.get("compromisso") or ("legado-" + str(r["id"])))

    rel = verificar_apuracao(aberturas, compromissos, candidatos_validos)
    rel["merkle_quadro"] = construir_merkle([c for c in compromissos if c])["raiz"]
    rel["falhas_decifra"] = falhas_decifra
    if falhas_decifra:
        rel["ok"] = False
    return rel


def _decifrar_votos_eleicao(eleicao_id: int) -> tuple[list[dict], list[dict]]:
    """
    Lê e decifra todos os votos de uma eleição (suporta votos legados em claro).
    Retorna (votos, falhas). Uma falha significa voto que NÃO pôde ser aberto —
    o chamador decide se aborta; ele nunca é silenciosamente contado como nulo.
    """
    from crypto_voto import decifrar_voto

    votos = []
    falhas = []
    with db_session() as conn:
        rows = conn.execute(
            """SELECT id, candidato_id, tipo_voto, cargo, peso, voto_cifrado, integridade
               FROM votos WHERE eleicao_id = ?""",
            (eleicao_id,),
        ).fetchall()

    for row in rows:
        r = dict(row)
        cargo_col = r.get("cargo") or ""
        if r.get("voto_cifrado"):
            try:
                dados = decifrar_voto(r["voto_cifrado"], r.get("integridade"))
            except ValueError as e:
                falhas.append({"voto_id": r["id"], "cargo": cargo_col, "erro": str(e)})
                continue

            cargo_aberto = dados.get("cargo") or cargo_col
            if cargo_aberto != cargo_col:
                falhas.append({
                    "voto_id": r["id"],
                    "cargo": cargo_col,
                    "erro": f"cargo divergente: coluna={cargo_col!r} abertura={cargo_aberto!r}",
                })
                continue

            votos.append({
                "id": r["id"],
                "tipo": dados["tipo"],
                "candidato_id": dados.get("candidato_id"),
                "cargo": cargo_aberto,
                "peso": float(dados.get("peso") or r.get("peso") or 1.0),
                "cifrado": True,
            })
        else:
            # Voto legado (anterior à criptografia)
            votos.append({
                "id": r["id"],
                "tipo": r.get("tipo_voto") or "nulo",
                "candidato_id": r.get("candidato_id"),
                "cargo": cargo_col,
                "peso": float(r.get("peso") or 1.0),
                "cifrado": False,
            })
    return votos, falhas


def obter_resultados(eleicao_id: int, estrito: bool = True) -> dict:
    """
    Calcula e retorna os resultados da eleição, cargo a cargo.
    Decifra os votos em memória para apurar (permanecem cifrados no disco).

    estrito=True (padrão): se algum voto não puder ser decifrado, levanta
    ErroApuracao em vez de contá-lo como nulo. Perder a chave `urna.key` ou
    ter dados corrompidos precisa ser um erro visível, não um resultado errado.
    """
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    ponderado = bool(eleicao.get("voto_ponderado"))
    votos, falhas = _decifrar_votos_eleicao(eleicao_id)

    if falhas and estrito:
        raise ErroApuracao(
            f"{len(falhas)} voto(s) não puderam ser decifrados ou estão inconsistentes. "
            f"Verifique a chave 'urna.key' e a integridade do banco antes de apurar.",
            falhas,
        )

    # Contagem por candidato e agregados por cargo
    contagem: dict[int, dict] = {}
    agregado_cargo: dict[str, dict] = {}
    for cargo in listar_cargos(eleicao_id):
        agregado_cargo[cargo] = {
            "brancos": {"qtd": 0, "peso": 0.0},
            "nulos": {"qtd": 0, "peso": 0.0},
            "total_votos": 0,
            "total_peso": 0.0,
        }

    for v in votos:
        cargo = v["cargo"] or "(sem cargo)"
        ag = agregado_cargo.setdefault(cargo, {
            "brancos": {"qtd": 0, "peso": 0.0},
            "nulos": {"qtd": 0, "peso": 0.0},
            "total_votos": 0,
            "total_peso": 0.0,
        })
        ag["total_votos"] += 1
        ag["total_peso"] += v["peso"]

        if v["tipo"] == "candidato" and v.get("candidato_id"):
            cid = v["candidato_id"]
            stats = contagem.setdefault(cid, {"votos": 0, "peso_votos": 0.0})
            stats["votos"] += 1
            stats["peso_votos"] += v["peso"]
        elif v["tipo"] == "branco":
            ag["brancos"]["qtd"] += 1
            ag["brancos"]["peso"] += v["peso"]
        else:
            ag["nulos"]["qtd"] += 1
            ag["nulos"]["peso"] += v["peso"]

    with db_session() as conn:
        cursor = conn.cursor()
        candidatos = []
        for row in cursor.execute(
            """SELECT id, numero, nome, cargo FROM candidatos
               WHERE eleicao_id = ? AND ativo = 1 ORDER BY cargo, numero""",
            (eleicao_id,),
        ).fetchall():
            c = dict(row)
            stats = contagem.get(c["id"], {"votos": 0, "peso_votos": 0.0})
            c["votos"] = stats["votos"]
            c["peso_votos"] = stats["peso_votos"]
            candidatos.append(c)

        candidatos.sort(
            key=lambda c: (c["cargo"], -(c["peso_votos"] if ponderado else c["votos"]), c["numero"])
        )

        row = cursor.execute(
            """SELECT COUNT(*) as total, COALESCE(SUM(peso), 0) as peso_total
               FROM eleitores WHERE eleicao_id = ? AND ativo = 1""",
            (eleicao_id,),
        ).fetchone()
        total_eleitores, peso_eleitores = row["total"], row["peso_total"]

        # "Compareceu" = votou em pelo menos um cargo
        row = cursor.execute(
            """SELECT COUNT(DISTINCT p.eleitor_id) as total,
                      COALESCE(SUM(e.peso), 0) as peso_total
               FROM (SELECT DISTINCT eleitor_id FROM participacao WHERE eleicao_id = ?) p
               JOIN eleitores e ON e.id = p.eleitor_id AND e.ativo = 1""",
            (eleicao_id,),
        ).fetchone()
        compareceram, peso_compareceram = row["total"], row["peso_total"]

        concluiram = cursor.execute(
            "SELECT COUNT(*) as t FROM eleitores WHERE eleicao_id = ? AND ja_votou = 1 AND ativo = 1",
            (eleicao_id,),
        ).fetchone()["t"]

    por_cargo: dict[str, list] = {}
    for c in candidatos:
        por_cargo.setdefault(c["cargo"], []).append(c)

    # Totais globais (soma de todos os cargos)
    brancos_qtd = sum(a["brancos"]["qtd"] for a in agregado_cargo.values())
    brancos_peso = sum(a["brancos"]["peso"] for a in agregado_cargo.values())
    nulos_qtd = sum(a["nulos"]["qtd"] for a in agregado_cargo.values())
    nulos_peso = sum(a["nulos"]["peso"] for a in agregado_cargo.values())

    return {
        "ponderado": ponderado,
        "cargos": listar_cargos(eleicao_id),
        "total_votos": len(votos),
        "total_peso": sum(v["peso"] for v in votos),
        "total_eleitores": total_eleitores,
        "peso_eleitores": peso_eleitores,
        "votaram": compareceram,
        "peso_votaram": peso_compareceram,
        "concluiram_todos_cargos": concluiram,
        "abstencoes": total_eleitores - compareceram,
        "candidatos": candidatos,
        "por_cargo": por_cargo,
        "agregado_cargo": agregado_cargo,
        "brancos": {"qtd": brancos_qtd, "peso": brancos_peso},
        "nulos": {"qtd": nulos_qtd, "peso": nulos_peso},
        "votos_cifrados": sum(1 for v in votos if v.get("cifrado")),
        "falhas_decifra": falhas,
    }


def relatorio_votos(eleicao_id: int, mascarar_cpf_saida: bool = False) -> dict:
    """
    Relatório de votos / presença.
    Lista quem já votou (nome, CPF, unidade, data) e em quantos cargos,
    SEM revelar em quem votou — o sigilo do voto é preservado.
    """
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    cargos = listar_cargos(eleicao_id)
    eleitores = listar_eleitores(eleicao_id, mascarar=mascarar_cpf_saida)

    with db_session() as conn:
        total_votos = conn.execute(
            "SELECT COUNT(*) as qtd FROM votos WHERE eleicao_id = ?", (eleicao_id,)
        ).fetchone()["qtd"]
        participacoes = {
            r["eleitor_id"]: r["qtd"] for r in conn.execute(
                """SELECT eleitor_id, COUNT(*) as qtd FROM participacao
                   WHERE eleicao_id = ? GROUP BY eleitor_id""",
                (eleicao_id,),
            ).fetchall()
        }

    for e in eleitores:
        e["cargos_votados_qtd"] = participacoes.get(e["id"], 0)
        e["total_cargos"] = len(cargos)
        e["completo"] = bool(cargos) and e["cargos_votados_qtd"] >= len(cargos)

    votaram = [e for e in eleitores if e["cargos_votados_qtd"] > 0]
    parciais = [e for e in votaram if not e["completo"]]
    pendentes = [e for e in eleitores if e["cargos_votados_qtd"] == 0]

    return {
        "eleicao": eleicao,
        "cargos": cargos,
        "total_eleitores": len(eleitores),
        "total_votos_computados": total_votos,
        "votaram": votaram,
        "parciais": parciais,
        "pendentes": pendentes,
        "qtd_votaram": len(votaram),
        "qtd_parciais": len(parciais),
        "qtd_pendentes": len(pendentes),
    }


# ==================== IMPORTAÇÃO ====================

def importar_eleitores_csv(eleicao_id: int, caminho: str) -> dict:
    """
    Importa eleitores de um arquivo CSV.

    Cabeçalho aceito (qualquer ordem, acentos e caixa livres):
        nome;cpf;unidade;bloco;peso
    Aceita ';' ou ',' como separador. Linhas com erro são reportadas
    individualmente e não interrompem a importação.
    """
    if not os.path.isfile(caminho):
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")

    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")
    if eleicao["status"] == "fechada":
        raise ValueError("Não é possível importar eleitores em eleição fechada.")

    def _norm(s: str) -> str:
        s = (s or "").strip().lower()
        for a, b in (("á", "a"), ("ã", "a"), ("â", "a"), ("é", "e"), ("ê", "e"),
                     ("í", "i"), ("ó", "o"), ("ô", "o"), ("ú", "u"), ("ç", "c")):
            s = s.replace(a, b)
        return s

    importados, erros = 0, []
    with open(caminho, "r", encoding="utf-8-sig", newline="") as f:
        amostra = f.read(4096)
        f.seek(0)
        delim = ";" if amostra.count(";") >= amostra.count(",") else ","
        leitor = csv.DictReader(f, delimiter=delim)

        if not leitor.fieldnames:
            raise ValueError("CSV vazio ou sem cabeçalho.")
        mapa = {_norm(c): c for c in leitor.fieldnames}
        col_nome = mapa.get("nome")
        col_cpf = mapa.get("cpf") or mapa.get("documento")
        if not col_nome or not col_cpf:
            raise ValueError(
                "O CSV precisa ter as colunas 'nome' e 'cpf'. "
                f"Colunas encontradas: {', '.join(leitor.fieldnames)}"
            )
        col_unid = mapa.get("unidade") or mapa.get("apartamento")
        col_bloco = mapa.get("bloco")
        col_peso = mapa.get("peso")

        for n, linha in enumerate(leitor, start=2):
            nome = (linha.get(col_nome) or "").strip()
            cpf = (linha.get(col_cpf) or "").strip()
            if not nome and not cpf:
                continue
            unidade = (linha.get(col_unid) or "").strip() if col_unid else ""
            bloco = (linha.get(col_bloco) or "").strip() if col_bloco else ""
            peso = 1.0
            if col_peso and (linha.get(col_peso) or "").strip():
                try:
                    peso = float(linha[col_peso].strip().replace(",", "."))
                except ValueError:
                    erros.append({"linha": n, "nome": nome, "erro": "peso inválido"})
                    continue
            try:
                cadastrar_eleitor(eleicao_id, nome, cpf, unidade=unidade,
                                  bloco=bloco, peso=peso)
                importados += 1
            except ValueError as e:
                erros.append({"linha": n, "nome": nome, "erro": str(e)})

    logger.info("Importação CSV: %s importados, %s erros (eleição %s)",
                importados, len(erros), eleicao_id)
    return {"importados": importados, "erros": erros, "total_erros": len(erros)}


def modelo_csv_eleitores(caminho: str | None = None) -> str:
    """Gera um CSV de exemplo para a importação de eleitores."""
    caminho = caminho or _caminho_saida("modelo_eleitores.csv")
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["nome", "cpf", "unidade", "bloco", "peso"])
        w.writerow(["Carlos Mendes", "123.456.789-09", "101", "A", "1.0"])
        w.writerow(["Fernanda Lima", "234.567.890-92", "202", "A", "1.5"])
    return caminho


# ==================== EXPORTAÇÃO ====================

def _nome_seguro(nome: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in nome)[:40]


def _caminho_saida(nome_arquivo: str) -> str:
    """Pasta de saída dos relatórios (ao lado do banco), criada se preciso."""
    from database import DB_PATH
    pasta = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "relatorios")
    os.makedirs(pasta, exist_ok=True)
    return os.path.join(pasta, nome_arquivo)


def exportar_relatorio_votos_csv(eleicao_id: int, caminho: str | None = None) -> str:
    """Exporta relatório de presença/votos para CSV."""
    rel = relatorio_votos(eleicao_id)
    eleicao = rel["eleicao"]

    if not caminho:
        caminho = _caminho_saida(
            f"relatorio_votos_{_nome_seguro(eleicao['nome'])}_{eleicao_id}.csv"
        )

    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["RELATÓRIO DE VOTOS / PRESENÇA"])
        writer.writerow(["Eleição", eleicao["nome"]])
        writer.writerow(["Status", eleicao["status"]])
        writer.writerow(["Cargos", ", ".join(rel["cargos"])])
        writer.writerow(["Total eleitores", rel["total_eleitores"]])
        writer.writerow(["Compareceram", rel["qtd_votaram"]])
        writer.writerow(["Votação parcial", rel["qtd_parciais"]])
        writer.writerow(["Pendentes", rel["qtd_pendentes"]])
        writer.writerow(["Votos computados", rel["total_votos_computados"]])
        writer.writerow([])
        writer.writerow(["OBS: Este relatório NÃO revela em quem cada pessoa votou (sigilo)."])
        writer.writerow([])
        writer.writerow(["STATUS", "NOME", "CPF", "UNIDADE", "BLOCO", "PESO",
                         "CARGOS VOTADOS", "TOTAL CARGOS", "DATA DO VOTO"])

        for e in rel["votaram"]:
            writer.writerow([
                "COMPLETO" if e["completo"] else "PARCIAL",
                e["nome"], e["cpf_formatado"],
                e["unidade"] or "", e["bloco"] or "",
                f"{e['peso']:.2f}", e["cargos_votados_qtd"], e["total_cargos"],
                e["data_voto"] or ""
            ])
        for e in rel["pendentes"]:
            writer.writerow([
                "PENDENTE", e["nome"], e["cpf_formatado"],
                e["unidade"] or "", e["bloco"] or "",
                f"{e['peso']:.2f}", 0, e["total_cargos"], ""
            ])

    return caminho


def exportar_resultados_csv(eleicao_id: int, caminho: str | None = None) -> str:
    """Exporta resultados para arquivo CSV. Retorna o caminho do arquivo."""
    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    resultados = obter_resultados(eleicao_id)

    if not caminho:
        caminho = _caminho_saida(
            f"resultados_{_nome_seguro(eleicao['nome'])}_{eleicao_id}.csv"
        )

    ponderado = resultados["ponderado"]
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["RESULTADOS DA ELEIÇÃO"])
        writer.writerow(["Nome", eleicao["nome"]])
        writer.writerow(["Tipo", eleicao["tipo"]])
        writer.writerow(["Status", eleicao["status"]])
        writer.writerow(["Voto Ponderado", "Sim" if ponderado else "Não"])
        writer.writerow([])
        writer.writerow(["RESUMO"])
        writer.writerow(["Total de eleitores aptos", resultados["total_eleitores"]])
        writer.writerow(["Compareceram (≥1 cargo)", resultados["votaram"]])
        writer.writerow(["Votaram em todos os cargos", resultados["concluiram_todos_cargos"]])
        writer.writerow(["Abstenções", resultados["abstencoes"]])
        writer.writerow(["Total de votos computados", resultados["total_votos"]])
        if ponderado:
            writer.writerow(["Peso total dos votos", f"{resultados['total_peso']:.2f}"])
            writer.writerow(["Peso dos eleitores aptos", f"{resultados['peso_eleitores']:.2f}"])
        writer.writerow([])
        writer.writerow(["CARGO", "NÚMERO", "NOME", "VOTOS", "PESO", "PERCENTUAL"])

        for cargo, lista in resultados["por_cargo"].items():
            ag = resultados["agregado_cargo"].get(cargo, {})
            total_cargo = ag.get("total_peso" if ponderado else "total_votos", 0)
            for c in lista:
                valor = c["peso_votos"] if ponderado else c["votos"]
                pct = (valor / total_cargo * 100) if total_cargo else 0
                writer.writerow([
                    cargo, c["numero"], c["nome"], c["votos"],
                    f"{c['peso_votos']:.2f}", f"{pct:.1f}%"
                ])
            writer.writerow([
                cargo, "", "BRANCO", ag.get("brancos", {}).get("qtd", 0),
                f"{ag.get('brancos', {}).get('peso', 0):.2f}", ""
            ])
            writer.writerow([
                cargo, "", "NULO", ag.get("nulos", {}).get("qtd", 0),
                f"{ag.get('nulos', {}).get('peso', 0):.2f}", ""
            ])
            writer.writerow([])

    return caminho


def exportar_resultados_pdf(eleicao_id: int, caminho: str | None = None) -> str:
    """Exporta resultados para arquivo PDF. Retorna o caminho do arquivo."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        from reportlab.lib.enums import TA_CENTER
    except ImportError as e:
        raise RuntimeError(
            "A exportação em PDF precisa da biblioteca reportlab. "
            "Instale com: pip install -r requirements.txt"
        ) from e

    eleicao = obter_eleicao(eleicao_id)
    if not eleicao:
        raise ValueError("Eleição não encontrada.")

    resultados = obter_resultados(eleicao_id)
    ponderado = resultados["ponderado"]

    if not caminho:
        caminho = _caminho_saida(
            f"resultados_{_nome_seguro(eleicao['nome'])}_{eleicao_id}.pdf"
        )

    doc = SimpleDocTemplate(
        caminho, pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=1.5*cm, bottomMargin=1.5*cm
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Titulo", parent=styles["Heading1"], alignment=TA_CENTER, fontSize=16, spaceAfter=6))
    styles.add(ParagraphStyle(name="Subtitulo", parent=styles["Normal"], alignment=TA_CENTER, fontSize=11, textColor=colors.grey, spaceAfter=12))
    styles.add(ParagraphStyle(name="Secao", parent=styles["Heading2"], fontSize=12, spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="Normal2", parent=styles["Normal"], fontSize=10, spaceAfter=3))

    elementos = []

    elementos.append(Paragraph("URNA ELETRÔNICA — BOLETIM DE RESULTADOS", styles["Titulo"]))
    elementos.append(Paragraph(eleicao["nome"], styles["Subtitulo"]))
    elementos.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#333333")))
    elementos.append(Spacer(1, 8))

    tipo_label = {"condominio": "Condomínio", "associacao": "Associação", "sindicato": "Sindicato"}.get(eleicao["tipo"], eleicao["tipo"])
    elementos.append(Paragraph(f"<b>Tipo:</b> {tipo_label} &nbsp;&nbsp; <b>Status:</b> {eleicao['status'].upper()}", styles["Normal2"]))
    elementos.append(Paragraph(f"<b>Voto ponderado:</b> {'Sim' if ponderado else 'Não'}", styles["Normal2"]))
    elementos.append(Paragraph(f"<b>Cargos:</b> {', '.join(resultados['cargos']) or '—'}", styles["Normal2"]))
    if eleicao.get("data_abertura"):
        elementos.append(Paragraph(f"<b>Abertura:</b> {eleicao['data_abertura']}", styles["Normal2"]))
    if eleicao.get("data_fechamento"):
        elementos.append(Paragraph(f"<b>Fechamento:</b> {eleicao['data_fechamento']}", styles["Normal2"]))

    elementos.append(Paragraph("Resumo da Apuração", styles["Secao"]))

    dados_resumo = [
        ["Total de eleitores aptos", str(resultados["total_eleitores"])],
        ["Compareceram (ao menos 1 cargo)", str(resultados["votaram"])],
        ["Votaram em todos os cargos", str(resultados["concluiram_todos_cargos"])],
        ["Abstenções", str(resultados["abstencoes"])],
        ["Total de votos computados", str(resultados["total_votos"])],
    ]
    if ponderado:
        dados_resumo.append(["Peso total dos votos", f"{resultados['total_peso']:.2f}"])
        dados_resumo.append(["Peso dos eleitores aptos", f"{resultados['peso_eleitores']:.2f}"])

    tabela_resumo = Table(dados_resumo, colWidths=[10*cm, 5*cm])
    tabela_resumo.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f5")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("PADDING", (0, 0), (-1, -1), 6),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    elementos.append(tabela_resumo)

    for cargo, lista in resultados["por_cargo"].items():
        elementos.append(Paragraph(f"Cargo: {cargo}", styles["Secao"]))

        ag = resultados["agregado_cargo"].get(cargo, {})
        total_cargo = ag.get("total_peso" if ponderado else "total_votos", 0)

        cabecalho = ["Pos.", "Nº", "Nome", "Votos"]
        if ponderado:
            cabecalho.append("Peso")
        cabecalho.append("%")

        dados = [cabecalho]
        for i, c in enumerate(lista, 1):
            valor = c["peso_votos"] if ponderado else c["votos"]
            pct = (valor / total_cargo * 100) if total_cargo else 0
            linha = [str(i), str(c["numero"]), c["nome"], str(c["votos"])]
            if ponderado:
                linha.append(f"{c['peso_votos']:.2f}")
            linha.append(f"{pct:.1f}%")
            dados.append(linha)

        col_widths = [1.2*cm, 1.5*cm, 7*cm, 2*cm]
        if ponderado:
            col_widths.append(2*cm)
        col_widths.append(1.8*cm)

        tabela = Table(dados, colWidths=col_widths)
        tabela.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#d5f5e3")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
            ("PADDING", (0, 0), (-1, -1), 5),
            ("ALIGN", (0, 0), (1, -1), "CENTER"),
            ("ALIGN", (-2, 0), (-1, -1), "CENTER"),
        ]))
        elementos.append(tabela)
        elementos.append(Spacer(1, 4))
        elementos.append(Paragraph(
            f"<b>Brancos:</b> {ag.get('brancos', {}).get('qtd', 0)} "
            f"(peso {ag.get('brancos', {}).get('peso', 0):.2f}) &nbsp;&nbsp; "
            f"<b>Nulos:</b> {ag.get('nulos', {}).get('qtd', 0)} "
            f"(peso {ag.get('nulos', {}).get('peso', 0):.2f})",
            styles["Normal2"]
        ))

    quadro = quadro_compromissos(eleicao_id)
    elementos.append(Spacer(1, 12))
    elementos.append(Paragraph(
        f"<b>Raiz Merkle do quadro de compromissos:</b><br/>"
        f"<font size=7>{quadro['merkle_raiz']}</font>",
        styles["Normal2"]
    ))

    elementos.append(Spacer(1, 20))
    elementos.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    elementos.append(Paragraph(
        f"Documento gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')} — Sistema de Urna Eletrônica",
        ParagraphStyle(name="Rodape", parent=styles["Normal"], fontSize=8, textColor=colors.grey, alignment=TA_CENTER)
    ))

    doc.build(elementos)
    return caminho
