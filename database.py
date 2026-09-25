"""
Módulo de gerenciamento do banco de dados SQLite
para o Sistema de Urna Eletrônica

Caminho do banco (prioridade):
  1. Variável de ambiente URNA_DB
  2. Arquivo urna.db na pasta do sistema
"""

import hashlib
import hmac
import logging
import os
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

# Permite sobrescrever o caminho do banco via variável de ambiente URNA_DB
_DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "urna.db")
DB_PATH = os.environ.get("URNA_DB", _DEFAULT_DB)

# Parâmetros de derivação de senha
PBKDF2_ITERACOES = 240_000

# Política de tentativas de login
MAX_TENTATIVAS_LOGIN = 5
JANELA_TENTATIVAS_SEG = 300      # 5 minutos
BLOQUEIO_LOGIN_SEG = 300         # 5 minutos de bloqueio após estourar

# Duração de uma sessão administrativa
TTL_SESSAO_ADMIN_SEG = 8 * 3600


# ==================== LOG EM ARQUIVO ====================

def _caminho_log() -> str:
    base = os.path.dirname(os.path.abspath(DB_PATH))
    return os.path.join(base, "urna.log")


logger = logging.getLogger("urna")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    try:
        _h = logging.FileHandler(_caminho_log(), encoding="utf-8")
        _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(_h)
    except OSError:
        pass


# ==================== CONEXÃO ====================

def _pasta_backups() -> str:
    """Pasta de backups (ao lado do arquivo do banco)."""
    base = os.path.dirname(os.path.abspath(DB_PATH))
    pasta = os.path.join(base, "backups")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def get_connection():
    """
    Retorna uma conexão com o banco SQLite configurada para uso concorrente:
    WAL (leitores não bloqueiam o escritor) e espera de até 30s por lock.
    """
    pasta = os.path.dirname(os.path.abspath(DB_PATH))
    if pasta:
        os.makedirs(pasta, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # Controle explícito de transação (necessário para BEGIN IMMEDIATE)
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.DatabaseError:
        # Bancos em sistemas de arquivos que não suportam WAL continuam funcionando
        pass
    return conn


@contextmanager
def db_session(escrita: bool = False):
    """
    Context manager para sessões de banco de dados.

    escrita=True abre uma transação BEGIN IMMEDIATE, garantindo que o bloco
    inteiro seja atômico e serializado contra outros escritores. Use sempre
    que a operação envolver mais de um comando de escrita (ex: registrar voto
    + marcar participação + auditoria).
    """
    conn = get_connection()
    try:
        if escrita:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if escrita:
            conn.execute("COMMIT")
    except Exception:
        if escrita:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        conn.close()


# ==================== SENHAS ====================

def hash_senha(senha: str, salt: str | None = None, iteracoes: int = PBKDF2_ITERACOES) -> str:
    """
    Deriva a senha com PBKDF2-HMAC-SHA256 e salt aleatório.
    Formato: pbkdf2_sha256$<iteracoes>$<salt_hex>$<hash_hex>
    """
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), iteracoes)
    return f"pbkdf2_sha256${iteracoes}${salt}${dk.hex()}"


def _hash_legado(senha: str) -> str:
    """SHA-256 puro — formato antigo, mantido só para migrar bancos existentes."""
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()


def hash_e_legado(armazenado: str) -> bool:
    """True se o hash guardado ainda está no formato antigo (SHA-256 sem salt)."""
    return not (armazenado or "").startswith("pbkdf2_sha256$")


def verificar_senha(senha: str, armazenado: str) -> bool:
    """Confere a senha contra o hash guardado, aceitando o formato legado."""
    if not armazenado:
        return False
    if hash_e_legado(armazenado):
        return hmac.compare_digest(_hash_legado(senha), armazenado)
    try:
        _, iteracoes, salt, esperado = armazenado.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), int(iteracoes))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), esperado)


def _coluna_existe(cursor, tabela: str, coluna: str) -> bool:
    """Verifica se uma coluna existe na tabela."""
    cursor.execute(f"PRAGMA table_info([{tabela}])")
    return any(row["name"] == coluna for row in cursor.fetchall())


def _tabela_existe(cursor, tabela: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (tabela,)
    )
    return cursor.fetchone() is not None


# ==================== BACKUP ====================

def fazer_backup(motivo: str = "manual") -> str:
    """
    Cria uma cópia de segurança consistente do urna.db (inclui o WAL).
    Retorna o caminho do arquivo de backup.
    Mantém no máximo 20 backups (remove os mais antigos).
    """
    if not os.path.isfile(DB_PATH):
        raise FileNotFoundError(f"Banco não encontrado: {DB_PATH}")

    pasta = _pasta_backups()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"urna_{stamp}_{motivo}.db"
    destino = os.path.join(pasta, nome)

    # sqlite3.Connection.backup copia inclusive as páginas ainda no WAL,
    # ao contrário de um simples copy2 do arquivo .db
    origem = get_connection()
    try:
        alvo = sqlite3.connect(destino)
        try:
            origem.backup(alvo)
        finally:
            alvo.close()
    finally:
        origem.close()

    # Limita a 20 backups
    backups = sorted(
        [os.path.join(pasta, f) for f in os.listdir(pasta) if f.endswith(".db")],
        key=os.path.getmtime
    )
    while len(backups) > 20:
        antigo = backups.pop(0)
        try:
            os.remove(antigo)
        except OSError:
            pass

    logger.info("Backup criado (%s): %s", motivo, destino)
    return destino


def listar_backups() -> list[dict]:
    """Lista backups disponíveis (mais recentes primeiro)."""
    pasta = _pasta_backups()
    itens = []
    for f in os.listdir(pasta):
        if f.endswith(".db"):
            caminho = os.path.join(pasta, f)
            itens.append({
                "nome": f,
                "caminho": caminho,
                "tamanho_kb": round(os.path.getsize(caminho) / 1024, 1),
                "modificado": datetime.fromtimestamp(os.path.getmtime(caminho)).isoformat(timespec="seconds"),
            })
    itens.sort(key=lambda x: x["modificado"], reverse=True)
    return itens


def restaurar_backup(caminho_backup: str) -> str:
    """
    Restaura um backup sobre o banco atual.
    Faz backup automático do estado atual antes de restaurar.
    """
    if not os.path.isfile(caminho_backup):
        raise FileNotFoundError(f"Backup não encontrado: {caminho_backup}")

    # Valida que o arquivo é mesmo um banco da urna
    teste = sqlite3.connect(caminho_backup)
    try:
        teste.row_factory = sqlite3.Row
        nomes = {
            r["name"] for r in teste.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        teste.close()
    if "eleicoes" not in nomes or "votos" not in nomes:
        raise ValueError("O arquivo informado não parece ser um banco da urna.")

    # Protege o estado atual
    if os.path.isfile(DB_PATH):
        fazer_backup("antes_restaurar")

    # Remove arquivos auxiliares do WAL para não misturar estados
    for sufixo in ("-wal", "-shm"):
        aux = DB_PATH + sufixo
        if os.path.isfile(aux):
            try:
                os.remove(aux)
            except OSError:
                pass

    shutil.copy2(caminho_backup, DB_PATH)
    logger.warning("Banco restaurado a partir de %s", caminho_backup)
    return DB_PATH


# ==================== INSPEÇÃO ====================

def inspecionar_banco() -> dict:
    """
    Retorna informações sobre o banco SQLite:
    caminho, tamanho, tabelas e quantidade de registros.
    """
    info = {
        "caminho": os.path.abspath(DB_PATH),
        "existe": os.path.isfile(DB_PATH),
        "tamanho_kb": 0,
        "tabelas": [],
    }
    if not info["existe"]:
        return info

    info["tamanho_kb"] = round(os.path.getsize(DB_PATH) / 1024, 1)

    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        for row in cursor.fetchall():
            nome = row["name"]
            cursor.execute(f"SELECT COUNT(*) as total FROM [{nome}]")
            total = cursor.fetchone()["total"]
            cursor.execute(f"PRAGMA table_info([{nome}])")
            colunas = [c["name"] for c in cursor.fetchall()]
            info["tabelas"].append({
                "nome": nome,
                "registros": total,
                "colunas": colunas,
            })

    return info


def inicializar_banco():
    """Cria as tabelas necessárias se não existirem e aplica migrações."""
    with db_session(escrita=True) as conn:
        cursor = conn.cursor()

        # Tabela de administradores
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS administradores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT UNIQUE NOT NULL,
                senha_hash TEXT NOT NULL,
                nome TEXT NOT NULL,
                trocar_senha INTEGER DEFAULT 0,
                ativo INTEGER DEFAULT 1,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        if not _coluna_existe(cursor, "administradores", "trocar_senha"):
            cursor.execute("ALTER TABLE administradores ADD COLUMN trocar_senha INTEGER DEFAULT 0")
        if not _coluna_existe(cursor, "administradores", "ativo"):
            cursor.execute("ALTER TABLE administradores ADD COLUMN ativo INTEGER DEFAULT 1")

        # Tabela de eleições
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS eleicoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                descricao TEXT,
                tipo TEXT NOT NULL CHECK(tipo IN ('condominio', 'associacao', 'sindicato')),
                status TEXT NOT NULL DEFAULT 'preparacao'
                    CHECK(status IN ('preparacao', 'aberta', 'fechada')),
                data_criacao TEXT DEFAULT CURRENT_TIMESTAMP,
                data_abertura TEXT,
                data_fechamento TEXT,
                permite_voto_branco INTEGER DEFAULT 1,
                permite_voto_nulo INTEGER DEFAULT 1,
                voto_ponderado INTEGER DEFAULT 0,
                contadores_homo TEXT
            )
        """)

        # Migração: adiciona voto_ponderado se não existir
        if not _coluna_existe(cursor, "eleicoes", "voto_ponderado"):
            cursor.execute("ALTER TABLE eleicoes ADD COLUMN voto_ponderado INTEGER DEFAULT 0")
        if not _coluna_existe(cursor, "eleicoes", "contadores_homo"):
            cursor.execute("ALTER TABLE eleicoes ADD COLUMN contadores_homo TEXT")

        # Tabela de candidatos
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS candidatos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eleicao_id INTEGER NOT NULL,
                numero INTEGER NOT NULL,
                nome TEXT NOT NULL,
                cargo TEXT NOT NULL,
                descricao TEXT,
                ativo INTEGER DEFAULT 1,
                FOREIGN KEY (eleicao_id) REFERENCES eleicoes(id) ON DELETE CASCADE,
                UNIQUE(eleicao_id, numero)
            )
        """)

        # Tabela de eleitores
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS eleitores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eleicao_id INTEGER NOT NULL,
                nome TEXT NOT NULL,
                documento TEXT NOT NULL,
                unidade TEXT,
                bloco TEXT,
                codigo_acesso TEXT NOT NULL,
                peso REAL DEFAULT 1.0,
                ja_votou INTEGER DEFAULT 0,
                data_voto TEXT,
                ativo INTEGER DEFAULT 1,
                FOREIGN KEY (eleicao_id) REFERENCES eleicoes(id) ON DELETE CASCADE,
                UNIQUE(eleicao_id, documento),
                UNIQUE(eleicao_id, codigo_acesso)
            )
        """)

        # Migração: adiciona peso se não existir
        if not _coluna_existe(cursor, "eleitores", "peso"):
            cursor.execute("ALTER TABLE eleitores ADD COLUMN peso REAL DEFAULT 1.0")

        # Tabela de votos (cifrado + compromisso público)
        # tipo_voto/candidato_id legados: votos novos usam voto_cifrado + compromisso
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS votos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eleicao_id INTEGER NOT NULL,
                candidato_id INTEGER,
                tipo_voto TEXT,
                cargo TEXT NOT NULL DEFAULT '',
                peso REAL DEFAULT 1.0,
                voto_cifrado TEXT,
                integridade TEXT,
                compromisso TEXT,
                prova_or TEXT,
                registrado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (eleicao_id) REFERENCES eleicoes(id) ON DELETE CASCADE,
                FOREIGN KEY (candidato_id) REFERENCES candidatos(id) ON DELETE SET NULL
            )
        """)

        # Migrações
        if not _coluna_existe(cursor, "votos", "peso"):
            cursor.execute("ALTER TABLE votos ADD COLUMN peso REAL DEFAULT 1.0")
        if not _coluna_existe(cursor, "votos", "voto_cifrado"):
            cursor.execute("ALTER TABLE votos ADD COLUMN voto_cifrado TEXT")
        if not _coluna_existe(cursor, "votos", "integridade"):
            cursor.execute("ALTER TABLE votos ADD COLUMN integridade TEXT")
        if not _coluna_existe(cursor, "votos", "compromisso"):
            cursor.execute("ALTER TABLE votos ADD COLUMN compromisso TEXT")
        if not _coluna_existe(cursor, "votos", "prova_or"):
            cursor.execute("ALTER TABLE votos ADD COLUMN prova_or TEXT")
        if not _coluna_existe(cursor, "votos", "cargo"):
            cursor.execute("ALTER TABLE votos ADD COLUMN cargo TEXT NOT NULL DEFAULT ''")

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_votos_eleicao ON votos(eleicao_id, cargo)"
        )

        # Participação: registra QUE o eleitor votou em determinado cargo.
        # Não guarda em quem votou — o sigilo fica no voto cifrado, que não
        # tem qualquer referência ao eleitor. A UNIQUE é o que impede voto duplo.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS participacao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                eleicao_id INTEGER NOT NULL,
                eleitor_id INTEGER NOT NULL,
                cargo TEXT NOT NULL,
                registrado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (eleicao_id) REFERENCES eleicoes(id) ON DELETE CASCADE,
                FOREIGN KEY (eleitor_id) REFERENCES eleitores(id) ON DELETE CASCADE,
                UNIQUE(eleicao_id, eleitor_id, cargo)
            )
        """)

        # Sessões administrativas (substitui "login sem token" da API)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessoes_admin (
                token_hash TEXT PRIMARY KEY,
                admin_id INTEGER NOT NULL,
                criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                expira_em TEXT NOT NULL,
                origem TEXT,
                FOREIGN KEY (admin_id) REFERENCES administradores(id) ON DELETE CASCADE
            )
        """)

        # Sessões de voto remoto e nonces — persistidos para sobreviver a
        # reinício do servidor e para que o anti-replay não dependa de RAM
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS desafios_remotos (
                challenge_id TEXT PRIMARY KEY,
                eleicao_id INTEGER NOT NULL,
                expira_em REAL NOT NULL,
                tentativas INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessoes_remotas (
                token_id TEXT PRIMARY KEY,
                assinatura TEXT NOT NULL,
                eleicao_id INTEGER NOT NULL,
                eleitor_id INTEGER NOT NULL,
                cpf TEXT NOT NULL,
                expira_em REAL NOT NULL,
                usado INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nonces_remotos (
                nonce TEXT PRIMARY KEY,
                registrado_em REAL NOT NULL
            )
        """)

        # Rate limiting de autenticação (admin e CPF remoto)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tentativas_auth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                escopo TEXT NOT NULL,
                identificador TEXT NOT NULL,
                sucesso INTEGER DEFAULT 0,
                quando REAL NOT NULL
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_tentativas ON tentativas_auth(escopo, identificador, quando)"
        )

        # Tabela de log de auditoria
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                acao TEXT NOT NULL,
                detalhes TEXT,
                usuario TEXT,
                registrado_em TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Cria administrador padrão se não existir
        cursor.execute("SELECT COUNT(*) as total FROM administradores")
        if cursor.fetchone()["total"] == 0:
            cursor.execute(
                """INSERT INTO administradores (usuario, senha_hash, nome, trocar_senha)
                   VALUES (?, ?, ?, 1)""",
                ("admin", hash_senha("admin123"), "Administrador Principal")
            )
            registrar_auditoria(
                conn, "SISTEMA",
                "Administrador padrão criado (admin/admin123) — troca de senha obrigatória no 1º acesso"
            )


def registrar_auditoria(conn, acao: str, detalhes: str = "", usuario: str = "sistema"):
    """Registra uma ação no log de auditoria."""
    conn.execute(
        "INSERT INTO auditoria (acao, detalhes, usuario) VALUES (?, ?, ?)",
        (acao, detalhes, usuario)
    )


def consultar_auditoria(
    acao: str | None = None,
    busca: str | None = None,
    limite: int = 100,
) -> list[dict]:
    """
    Consulta o log de auditoria.
    - acao: filtra por tipo (ex: VOTO_REGISTRADO, ALTERAR_STATUS)
    - busca: texto livre em detalhes/usuario
    - limite: máximo de registros (mais recentes primeiro)
    """
    limite = max(1, min(int(limite), 20000))
    with db_session() as conn:
        cursor = conn.cursor()
        sql = "SELECT id, acao, detalhes, usuario, registrado_em FROM auditoria WHERE 1=1"
        params: list = []

        if acao:
            sql += " AND acao = ?"
            params.append(acao)
        if busca:
            sql += " AND (detalhes LIKE ? OR usuario LIKE ? OR acao LIKE ?)"
            like = f"%{busca}%"
            params.extend([like, like, like])

        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limite)

        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def listar_acoes_auditoria() -> list[dict]:
    """Retorna tipos de ação e quantidade de ocorrências."""
    with db_session() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT acao, COUNT(*) as total
               FROM auditoria
               GROUP BY acao
               ORDER BY total DESC"""
        )
        return [dict(row) for row in cursor.fetchall()]


def auditoria_consistencia_votos(eleicao_id: int) -> dict:
    """
    Cruzamento de integridade da eleição:
    - registros de participação (eleitor × cargo)
    - quantos votos na urna
    - eventos VOTO_REGISTRADO no log
    Não revela em quem cada um votou.
    """
    with db_session() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """SELECT COUNT(*) as total FROM participacao p
               JOIN eleitores e ON e.id = p.eleitor_id
               WHERE p.eleicao_id = ? AND e.ativo = 1""",
            (eleicao_id,),
        )
        participacoes = cursor.fetchone()["total"]

        cursor.execute(
            "SELECT COUNT(*) as total FROM votos WHERE eleicao_id = ?",
            (eleicao_id,),
        )
        votos_urna = cursor.fetchone()["total"]

        cursor.execute(
            """SELECT COUNT(*) as total FROM auditoria
               WHERE acao = 'VOTO_REGISTRADO' AND detalhes LIKE ?""",
            (f"%Eleição={eleicao_id} %",),
        )
        eventos_log = cursor.fetchone()["total"]

        cursor.execute(
            """SELECT COUNT(*) as total FROM eleitores
               WHERE eleicao_id = ? AND ja_votou = 1 AND ativo = 1""",
            (eleicao_id,),
        )
        eleitores_completos = cursor.fetchone()["total"]

        # Distribuição por cargo (não revela conteúdo do voto)
        cursor.execute(
            """SELECT cargo, COUNT(*) as qtd, COALESCE(SUM(peso), 0) as peso
               FROM votos WHERE eleicao_id = ?
               GROUP BY cargo""",
            (eleicao_id,),
        )
        por_cargo = {
            (row["cargo"] or "(sem cargo)"): {"qtd": row["qtd"], "peso": row["peso"]}
            for row in cursor.fetchall()
        }

        cursor.execute(
            """SELECT id, detalhes, usuario, registrado_em
               FROM auditoria
               WHERE acao = 'VOTO_REGISTRADO' AND detalhes LIKE ?
               ORDER BY id""",
            (f"%Eleição={eleicao_id} %",),
        )
        timeline = [dict(row) for row in cursor.fetchall()]

        consistente = (participacoes == votos_urna == eventos_log)

        return {
            "eleicao_id": eleicao_id,
            "participacoes": participacoes,
            "eleitores_completos": eleitores_completos,
            "votos_urna": votos_urna,
            "eventos_log": eventos_log,
            "por_cargo": por_cargo,
            "timeline": timeline,
            "consistente": consistente,
            "divergencia": {
                "participacao_vs_urna": participacoes - votos_urna,
                "urna_vs_log": votos_urna - eventos_log,
            },
        }


def exportar_auditoria_csv(caminho: str, registros: list[dict]) -> str:
    """Exporta registros de auditoria para CSV."""
    import csv
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["ID", "DATA/HORA", "AÇÃO", "USUÁRIO", "DETALHES"])
        for r in registros:
            writer.writerow([
                r.get("id"),
                r.get("registrado_em"),
                r.get("acao"),
                r.get("usuario"),
                r.get("detalhes"),
            ])
    return caminho


# ==================== RATE LIMITING ====================

def registrar_tentativa(escopo: str, identificador: str, sucesso: bool):
    """Anota uma tentativa de autenticação para o controle de força bruta."""
    agora = time.time()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO tentativas_auth (escopo, identificador, sucesso, quando) VALUES (?, ?, ?, ?)",
            (escopo, (identificador or "")[:120], 1 if sucesso else 0, agora),
        )
        # Limpeza oportunista
        conn.execute(
            "DELETE FROM tentativas_auth WHERE quando < ?",
            (agora - 24 * 3600,),
        )


def bloqueado_por_tentativas(escopo: str, identificador: str) -> float:
    """
    Retorna quantos segundos ainda faltam de bloqueio (0 = liberado).
    Conta falhas consecutivas dentro da janela.
    """
    agora = time.time()
    with db_session() as conn:
        rows = conn.execute(
            """SELECT sucesso, quando FROM tentativas_auth
               WHERE escopo = ? AND identificador = ? AND quando > ?
               ORDER BY quando DESC LIMIT ?""",
            (escopo, (identificador or "")[:120], agora - JANELA_TENTATIVAS_SEG, MAX_TENTATIVAS_LOGIN),
        ).fetchall()

    if len(rows) < MAX_TENTATIVAS_LOGIN:
        return 0.0
    if any(r["sucesso"] for r in rows):
        return 0.0
    restante = (rows[0]["quando"] + BLOQUEIO_LOGIN_SEG) - agora
    return max(0.0, restante)


# ==================== ADMINISTRADORES ====================

def verificar_admin(usuario: str, senha: str, origem: str = "local") -> dict | None:
    """
    Verifica credenciais de administrador, com proteção contra força bruta.
    Migra automaticamente hashes no formato antigo.
    Retorna dados do admin (sem o hash) ou None.
    """
    usuario = (usuario or "").strip()
    espera = bloqueado_por_tentativas("admin", usuario)
    if espera > 0:
        raise PermissionError(
            f"Muitas tentativas. Tente novamente em {int(espera) + 1} segundos."
        )

    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM administradores WHERE usuario = ? AND ativo = 1",
            (usuario,),
        ).fetchone()

    ok = bool(row) and verificar_senha(senha, row["senha_hash"])
    registrar_tentativa("admin", usuario, ok)

    if not ok:
        logger.warning("Login administrativo negado (usuario=%s, origem=%s)", usuario, origem)
        return None

    admin = dict(row)

    # Migra hash legado para PBKDF2 de forma transparente
    if hash_e_legado(admin["senha_hash"]):
        with db_session(escrita=True) as conn:
            conn.execute(
                "UPDATE administradores SET senha_hash = ? WHERE id = ?",
                (hash_senha(senha), admin["id"]),
            )
            registrar_auditoria(
                conn, "MIGRAR_HASH_SENHA", f"Usuário={usuario}", usuario
            )
        logger.info("Hash de senha migrado para PBKDF2 (usuario=%s)", usuario)

    admin.pop("senha_hash", None)
    admin["trocar_senha"] = bool(admin.get("trocar_senha"))
    logger.info("Login administrativo OK (usuario=%s, origem=%s)", usuario, origem)
    return admin


def alterar_senha_admin(usuario: str, senha_atual: str, senha_nova: str) -> bool:
    """Altera a senha de um administrador. Retorna True se sucesso."""
    if len(senha_nova or "") < 8:
        raise ValueError("A nova senha deve ter pelo menos 8 caracteres.")
    if senha_nova == senha_atual:
        raise ValueError("A nova senha deve ser diferente da atual.")

    with db_session(escrita=True) as conn:
        row = conn.execute(
            "SELECT id, senha_hash FROM administradores WHERE usuario = ? AND ativo = 1",
            (usuario,),
        ).fetchone()
        if not row or not verificar_senha(senha_atual, row["senha_hash"]):
            return False
        conn.execute(
            "UPDATE administradores SET senha_hash = ?, trocar_senha = 0 WHERE id = ?",
            (hash_senha(senha_nova), row["id"]),
        )
        # Invalida sessões abertas desse admin
        conn.execute("DELETE FROM sessoes_admin WHERE admin_id = ?", (row["id"],))
        registrar_auditoria(conn, "ALTERAR_SENHA", f"Usuário={usuario}", usuario)
    logger.info("Senha alterada (usuario=%s)", usuario)
    return True


def criar_admin(usuario: str, senha: str, nome: str, autor: str = "sistema") -> int:
    """Cadastra um novo administrador."""
    usuario = (usuario or "").strip()
    if not usuario or not nome:
        raise ValueError("Usuário e nome são obrigatórios.")
    if len(senha or "") < 8:
        raise ValueError("A senha deve ter pelo menos 8 caracteres.")

    with db_session(escrita=True) as conn:
        existe = conn.execute(
            "SELECT id FROM administradores WHERE usuario = ?", (usuario,)
        ).fetchone()
        if existe:
            raise ValueError(f"Já existe um administrador com o usuário '{usuario}'.")
        cur = conn.execute(
            """INSERT INTO administradores (usuario, senha_hash, nome, trocar_senha)
               VALUES (?, ?, ?, 1)""",
            (usuario, hash_senha(senha), nome),
        )
        registrar_auditoria(conn, "CRIAR_ADMIN", f"Usuário={usuario} Nome={nome}", autor)
        return cur.lastrowid


def listar_admins() -> list[dict]:
    """Lista administradores (sem hashes)."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT id, usuario, nome, ativo, trocar_senha, criado_em
               FROM administradores ORDER BY id"""
        ).fetchall()
    return [dict(r) for r in rows]


def desativar_admin(usuario: str, autor: str = "sistema") -> bool:
    """Desativa um administrador (nunca remove, para preservar a auditoria)."""
    with db_session(escrita=True) as conn:
        restantes = conn.execute(
            "SELECT COUNT(*) as t FROM administradores WHERE ativo = 1 AND usuario <> ?",
            (usuario,),
        ).fetchone()["t"]
        if restantes == 0:
            raise ValueError("Não é possível desativar o último administrador ativo.")
        cur = conn.execute(
            "UPDATE administradores SET ativo = 0 WHERE usuario = ? AND ativo = 1",
            (usuario,),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            """DELETE FROM sessoes_admin WHERE admin_id IN
               (SELECT id FROM administradores WHERE usuario = ?)""",
            (usuario,),
        )
        registrar_auditoria(conn, "DESATIVAR_ADMIN", f"Usuário={usuario}", autor)
        return True


def redefinir_senha_admin(usuario: str, senha_nova: str, autor: str = "sistema") -> bool:
    """Redefine a senha de outro administrador, exigindo troca no próximo acesso."""
    if len(senha_nova or "") < 8:
        raise ValueError("A senha deve ter pelo menos 8 caracteres.")
    with db_session(escrita=True) as conn:
        cur = conn.execute(
            "UPDATE administradores SET senha_hash = ?, trocar_senha = 1 WHERE usuario = ? AND ativo = 1",
            (hash_senha(senha_nova), usuario),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            """DELETE FROM sessoes_admin WHERE admin_id IN
               (SELECT id FROM administradores WHERE usuario = ?)""",
            (usuario,),
        )
        registrar_auditoria(conn, "REDEFINIR_SENHA", f"Usuário={usuario}", autor)
        return True


# ==================== SESSÕES ADMINISTRATIVAS (API) ====================

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def criar_sessao_admin(admin_id: int, origem: str = "api") -> dict:
    """Emite um token de sessão administrativa. Só o hash é guardado."""
    token = secrets.token_urlsafe(32)
    expira = datetime.now() + timedelta(seconds=TTL_SESSAO_ADMIN_SEG)
    with db_session(escrita=True) as conn:
        conn.execute("DELETE FROM sessoes_admin WHERE expira_em < ?",
                     (datetime.now().isoformat(timespec="seconds"),))
        conn.execute(
            """INSERT INTO sessoes_admin (token_hash, admin_id, expira_em, origem)
               VALUES (?, ?, ?, ?)""",
            (_hash_token(token), admin_id, expira.isoformat(timespec="seconds"), origem),
        )
    return {"token": token, "expira_em": expira.isoformat(timespec="seconds"),
            "ttl": TTL_SESSAO_ADMIN_SEG}


def validar_sessao_admin(token: str) -> dict | None:
    """Valida um token administrativo. Retorna os dados do admin ou None."""
    if not token:
        return None
    agora = datetime.now().isoformat(timespec="seconds")
    with db_session() as conn:
        row = conn.execute(
            """SELECT a.id, a.usuario, a.nome, s.expira_em
               FROM sessoes_admin s
               JOIN administradores a ON a.id = s.admin_id
               WHERE s.token_hash = ? AND s.expira_em > ? AND a.ativo = 1""",
            (_hash_token(token), agora),
        ).fetchone()
    return dict(row) if row else None


def encerrar_sessao_admin(token: str) -> bool:
    with db_session() as conn:
        cur = conn.execute(
            "DELETE FROM sessoes_admin WHERE token_hash = ?", (_hash_token(token),)
        )
        return cur.rowcount > 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "inspecionar":
        inicializar_banco()
        info = inspecionar_banco()
        print(f"\n  Banco SQLite: {info['caminho']}")
        print(f"  Tamanho: {info['tamanho_kb']} KB")
        print(f"  Existe: {info['existe']}\n")
        if info["tabelas"]:
            print(f"  {'TABELA':<20} {'REGISTROS':>10}  COLUNAS")
            print("  " + "─" * 70)
            for t in info["tabelas"]:
                cols = ", ".join(t["colunas"][:6])
                if len(t["colunas"]) > 6:
                    cols += ", ..."
                print(f"  {t['nome']:<20} {t['registros']:>10}  {cols}")
        print()
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "backup":
        inicializar_banco()
        caminho = fazer_backup("manual")
        print(f"\n  Backup criado: {caminho}\n")
        sys.exit(0)

    if len(sys.argv) > 2 and sys.argv[1] == "restaurar":
        inicializar_banco()
        caminho = restaurar_backup(sys.argv[2])
        print(f"\n  Banco restaurado de {sys.argv[2]} para {caminho}\n")
        sys.exit(0)

    inicializar_banco()
    print("Banco de dados SQLite inicializado com sucesso!")
    print(f"Arquivo: {DB_PATH}")
    info = inspecionar_banco()
    print(f"Tamanho: {info['tamanho_kb']} KB | Tabelas: {len(info['tabelas'])}")
