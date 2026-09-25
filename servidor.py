#!/usr/bin/env python3
"""
Servidor central da Urna Eletrônica (rede local).

Uso:
  python3 servidor.py                  # escuta em 0.0.0.0:8080
  python3 servidor.py --porta 9000     # porta customizada
  python3 servidor.py --host 192.168.1.10 --porta 8080

Nas outras máquinas:
  - Votação:   python3 cliente_votacao.py --servidor IP_DO_SERVIDOR
  - Relatório: python3 cliente_relatorio.py --servidor IP_DO_SERVIDOR
"""

import argparse
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from socketserver import ThreadingMixIn

# Garante que os módulos locais sejam encontrados
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    inicializar_banco, verificar_admin,
    consultar_auditoria, listar_acoes_auditoria, auditoria_consistencia_votos,
)
from eleicao import (
    listar_eleicoes, obter_eleicao, listar_candidatos, obter_candidato_por_numero,
    autenticar_eleitor, marcar_como_votou, registrar_voto, obter_resultados,
    relatorio_votos, listar_eleitores, formatar_cpf, validar_cpf, normalizar_cpf,
    obter_recibo_voto,
)
from remoto_seguro import (
    criar_desafio, criar_sessao, validar_sessao, consumir_sessao,
)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Servidor HTTP multi-thread para várias estações simultâneas."""
    daemon_threads = True
    allow_reuse_address = True


def json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def ler_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


class UrnaHandler(BaseHTTPRequestHandler):
    """API REST da Urna Eletrônica."""

    def log_message(self, fmt, *args):
        # Log mais limpo
        print(f"  [{self.log_date_time_string()}] {args[0]}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        try:
            if path == "/api/status":
                return json_response(self, {
                    "ok": True,
                    "servico": "Urna Eletrônica",
                    "versao": "3.0-rede",
                    "mensagem": "Servidor online"
                })

            if path == "/api/eleicoes":
                status = qs.get("status", [None])[0]
                eleicoes = listar_eleicoes(status)
                return json_response(self, {"ok": True, "eleicoes": eleicoes})

            if path.startswith("/api/eleicoes/"):
                partes = path.split("/")
                # /api/eleicoes/1
                # /api/eleicoes/1/candidatos
                # /api/eleicoes/1/resultados
                # /api/eleicoes/1/relatorio-votos
                try:
                    eid = int(partes[3])
                except (IndexError, ValueError):
                    return json_response(self, {"ok": False, "erro": "ID inválido"}, 400)

                if len(partes) == 4:
                    eleicao = obter_eleicao(eid)
                    if not eleicao:
                        return json_response(self, {"ok": False, "erro": "Eleição não encontrada"}, 404)
                    return json_response(self, {"ok": True, "eleicao": eleicao})

                recurso = partes[4] if len(partes) > 4 else ""
                if recurso == "candidatos":
                    return json_response(self, {
                        "ok": True,
                        "candidatos": listar_candidatos(eid)
                    })
                if recurso == "resultados":
                    return json_response(self, {
                        "ok": True,
                        "resultados": obter_resultados(eid)
                    })
                if recurso == "relatorio-votos":
                    return json_response(self, {
                        "ok": True,
                        "relatorio": relatorio_votos(eid)
                    })
                if recurso == "eleitores":
                    return json_response(self, {
                        "ok": True,
                        "eleitores": listar_eleitores(eid)
                    })
                if recurso == "integridade":
                    return json_response(self, {
                        "ok": True,
                        "integridade": auditoria_consistencia_votos(eid)
                    })

            if path == "/api/auditoria":
                acao = qs.get("acao", [None])[0]
                busca = qs.get("busca", [None])[0]
                try:
                    limite = int(qs.get("limite", ["100"])[0])
                except ValueError:
                    limite = 100
                return json_response(self, {
                    "ok": True,
                    "registros": consultar_auditoria(acao=acao, busca=busca, limite=limite),
                    "resumo": listar_acoes_auditoria(),
                })

            return json_response(self, {"ok": False, "erro": "Rota não encontrada"}, 404)

        except Exception as e:
            return json_response(self, {"ok": False, "erro": str(e)}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        dados = ler_json(self)

        try:
            if path == "/api/admin/login":
                usuario = dados.get("usuario", "")
                senha = dados.get("senha", "")
                admin = verificar_admin(usuario, senha)
                if admin:
                    return json_response(self, {
                        "ok": True,
                        "admin": {"id": admin["id"], "usuario": admin["usuario"], "nome": admin["nome"]}
                    })
                return json_response(self, {"ok": False, "erro": "Usuário ou senha inválidos"}, 401)

            # --- Votação remota segura ---
            # 1) Desafio anti-replay
            if path == "/api/remoto/desafio":
                eid = int(dados.get("eleicao_id", 0))
                eleicao = obter_eleicao(eid)
                if not eleicao or eleicao["status"] != "aberta":
                    return json_response(self, {"ok": False, "erro": "Eleição não está aberta"}, 400)
                return json_response(self, {"ok": True, **criar_desafio(eid)})

            # 2) Autentica CPF + desafio → sessão de uso único
            if path == "/api/remoto/autenticar":
                eid = int(dados.get("eleicao_id", 0))
                cpf = dados.get("cpf", "")
                challenge_id = dados.get("challenge_id", "")
                if not eid or not cpf or not challenge_id:
                    return json_response(self, {
                        "ok": False,
                        "erro": "eleicao_id, cpf e challenge_id são obrigatórios"
                    }, 400)
                if not validar_cpf(cpf):
                    return json_response(self, {"ok": False, "erro": "CPF inválido (algoritmo oficial)"}, 400)
                eleitor = autenticar_eleitor(eid, cpf)
                if not eleitor:
                    return json_response(self, {"ok": False, "erro": "CPF não encontrado nesta eleição"}, 404)
                if eleitor["ja_votou"]:
                    return json_response(self, {
                        "ok": False,
                        "erro": "Este CPF já registrou voto nesta eleição",
                        "ja_votou": True,
                    }, 403)
                try:
                    sess = criar_sessao(
                        eid, eleitor["id"], normalizar_cpf(cpf), challenge_id
                    )
                except ValueError as e:
                    return json_response(self, {"ok": False, "erro": str(e)}, 403)
                return json_response(self, {
                    "ok": True,
                    "sessao": sess,
                    "eleitor": {
                        "id": eleitor["id"],
                        "nome": eleitor["nome"],
                        "cpf": formatar_cpf(eleitor["documento"]),
                        "unidade": eleitor.get("unidade"),
                        "bloco": eleitor.get("bloco"),
                        "peso": eleitor.get("peso", 1.0),
                    },
                })

            # 3) Voto com token de sessão + nonce
            if path == "/api/remoto/votar":
                eid = int(dados.get("eleicao_id", 0))
                eleitor_id = int(dados.get("eleitor_id", 0))
                token = dados.get("token", "")
                nonce = dados.get("nonce", "")
                tipo = dados.get("tipo_voto", "")
                numero = dados.get("numero_candidato")

                eleicao = obter_eleicao(eid)
                if not eleicao or eleicao["status"] != "aberta":
                    return json_response(self, {"ok": False, "erro": "Eleição não está aberta"}, 400)

                try:
                    validar_sessao(token, eid, eleitor_id)
                except ValueError as e:
                    return json_response(self, {"ok": False, "erro": str(e)}, 403)

                if not nonce or len(nonce) < 16:
                    return json_response(self, {"ok": False, "erro": "nonce obrigatório"}, 400)

                eleitor = autenticar_eleitor(eid, dados.get("cpf", ""))
                # Revalida por id da sessão (CPF opcional no corpo se já na sessão)
                from database import db_session
                with db_session() as conn:
                    row = conn.execute(
                        "SELECT * FROM eleitores WHERE id = ? AND eleicao_id = ?",
                        (eleitor_id, eid),
                    ).fetchone()
                if not row:
                    return json_response(self, {"ok": False, "erro": "Eleitor inválido"}, 403)
                eleitor = dict(row)
                if eleitor["ja_votou"]:
                    return json_response(self, {"ok": False, "erro": "CPF já votou"}, 403)

                peso = float(eleitor.get("peso") or 1.0)
                candidato_id = None
                if tipo == "candidato":
                    if numero is None:
                        return json_response(self, {"ok": False, "erro": "Número do candidato obrigatório"}, 400)
                    cand = obter_candidato_por_numero(eid, int(numero))
                    if not cand:
                        return json_response(self, {"ok": False, "erro": "Candidato não encontrado"}, 404)
                    candidato_id = cand["id"]
                elif tipo not in ("branco", "nulo"):
                    return json_response(self, {"ok": False, "erro": "tipo_voto inválido"}, 400)

                try:
                    consumir_sessao(token, nonce)
                except ValueError as e:
                    return json_response(self, {"ok": False, "erro": str(e)}, 403)

                voto_id = registrar_voto(eid, tipo, candidato_id, peso)
                marcar_como_votou(eleitor_id)
                recibo = obter_recibo_voto(voto_id)

                return json_response(self, {
                    "ok": True,
                    "mensagem": "Voto remoto registrado com sucesso",
                    "voto_id": voto_id,
                    "compromisso": (recibo or {}).get("compromisso"),
                    "tem_prova_or": (recibo or {}).get("tem_prova_or"),
                })

            # Compatibilidade: autenticação legada (local)
            if path == "/api/votar/autenticar":
                eid = dados.get("eleicao_id")
                cpf = dados.get("cpf", "")
                if not eid or not cpf:
                    return json_response(self, {"ok": False, "erro": "eleicao_id e cpf são obrigatórios"}, 400)
                if not validar_cpf(cpf):
                    return json_response(self, {"ok": False, "erro": "CPF inválido"}, 400)
                eleitor = autenticar_eleitor(int(eid), cpf)
                if not eleitor:
                    return json_response(self, {"ok": False, "erro": "CPF não encontrado"}, 404)
                if eleitor["ja_votou"]:
                    return json_response(self, {"ok": False, "erro": "Já votou", "ja_votou": True}, 403)
                return json_response(self, {
                    "ok": True,
                    "eleitor": {
                        "id": eleitor["id"],
                        "nome": eleitor["nome"],
                        "cpf": formatar_cpf(eleitor["documento"]),
                        "unidade": eleitor.get("unidade"),
                        "bloco": eleitor.get("bloco"),
                        "peso": eleitor.get("peso", 1.0),
                    }
                })

            return json_response(self, {"ok": False, "erro": "Rota não encontrada"}, 404)

        except Exception as e:
            return json_response(self, {"ok": False, "erro": str(e)}, 500)


def main():
    parser = argparse.ArgumentParser(description="Servidor da Urna Eletrônica")
    parser.add_argument("--host", default="0.0.0.0", help="Interface de rede (padrão: 0.0.0.0)")
    parser.add_argument("--porta", type=int, default=8080, help="Porta TCP (padrão: 8080)")
    parser.add_argument("--db", default=None, help="Caminho do arquivo SQLite (padrão: urna.db)")
    args = parser.parse_args()

    # Caminho customizado do banco SQLite
    if args.db:
        os.environ["URNA_DB"] = os.path.abspath(args.db)
        # Recarrega o caminho no módulo database
        import database
        database.DB_PATH = os.environ["URNA_DB"]

    from database import DB_PATH, inspecionar_banco, fazer_backup
    inicializar_banco()

    # Backup automático ao iniciar o servidor
    try:
        if os.path.isfile(DB_PATH):
            bk = fazer_backup("inicio_servidor")
            print(f"  Backup ao iniciar: {os.path.basename(bk)}")
    except Exception:
        pass

    server = ThreadedHTTPServer((args.host, args.porta), UrnaHandler)

    # Descobre IP local para mostrar nas instruções
    import socket
    ip_local = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_local = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    info = inspecionar_banco()
    print("""
╔══════════════════════════════════════════════════════════════╗
║         URNA ELETRÔNICA — SERVIDOR CENTRAL v3.0              ║
╚══════════════════════════════════════════════════════════════╝
""")
    print(f"  Escutando em:  http://{args.host}:{args.porta}")
    print(f"  IP deste PC:   {ip_local}")
    print(f"  Banco SQLite:  {info['caminho']} ({info['tamanho_kb']} KB)")
    print(f"  Status:        http://{ip_local}:{args.porta}/api/status")
    print()
    print("  Nas outras máquinas da rede:")
    print(f"    Votação:    python3 cliente_votacao.py --servidor {ip_local} --porta {args.porta}")
    print(f"    Relatório:  python3 cliente_relatorio.py --servidor {ip_local} --porta {args.porta}")
    print()
    print("  Sugestão para 10 máquinas:")
    print("    • 1  → este servidor")
    print("    • 2  → cliente_relatorio.py  (mesas de apuração/relatório)")
    print("    • 7  → cliente_votacao.py    (cabines de votação)")
    print()
    print("  Ctrl+C para parar o servidor.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor encerrado.\n")
        server.server_close()


if __name__ == "__main__":
    main()
