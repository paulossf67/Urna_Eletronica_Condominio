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

Autenticação
------------
Rotas que expõem dados pessoais ou resultados exigem um token de sessão
administrativa no cabeçalho `Authorization: Bearer <token>`, obtido em
POST /api/admin/login. Rotas públicas: status, lista de eleições,
candidatos, quadro de compromissos e conferência de recibo.
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
    inicializar_banco, verificar_admin, alterar_senha_admin,
    criar_sessao_admin, validar_sessao_admin, encerrar_sessao_admin,
    consultar_auditoria, listar_acoes_auditoria, auditoria_consistencia_votos,
    logger,
)
from eleicao import (
    listar_eleicoes, obter_eleicao, listar_candidatos, obter_candidato_por_numero,
    autenticar_eleitor, registrar_voto, obter_resultados,
    relatorio_votos, listar_eleitores, formatar_cpf, validar_cpf, normalizar_cpf,
    obter_recibo_voto, listar_cargos, cargos_pendentes, quadro_compromissos,
    verificar_recibo, ErroApuracao,
)
from remoto_seguro import (
    criar_desafio, criar_sessao, validar_sessao, consumir_sessao, encerrar_sessao,
)

MAX_CORPO_BYTES = 1 * 1024 * 1024  # 1 MB — nenhuma requisição legítima passa disso


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Servidor HTTP multi-thread para várias estações simultâneas."""
    daemon_threads = True
    allow_reuse_address = True


def json_response(handler, data, status=200):
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Cache-Control", "no-store")
    if handler.server.origem_permitida:
        handler.send_header("Access-Control-Allow-Origin", handler.server.origem_permitida)
    handler.end_headers()
    handler.wfile.write(body)


def ler_json(handler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", 0))
    except ValueError:
        return {}
    if length <= 0:
        return {}
    if length > MAX_CORPO_BYTES:
        raise ValueError("Corpo da requisição grande demais")
    raw = handler.rfile.read(length)
    try:
        dados = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _inteiro(valor, padrao=0) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


class UrnaHandler(BaseHTTPRequestHandler):
    """API REST da Urna Eletrônica."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Log mais limpo, e também no arquivo
        msg = args[0] if args else fmt
        print(f"  [{self.log_date_time_string()}] {msg}")
        logger.info("HTTP %s %s", self.client_address[0], msg)

    # ---------------- autenticação ----------------

    def _admin(self) -> dict | None:
        """Retorna o admin da sessão, ou None se o token faltar/for inválido."""
        cabecalho = self.headers.get("Authorization", "")
        if not cabecalho.lower().startswith("bearer "):
            return None
        return validar_sessao_admin(cabecalho[7:].strip())

    def _exigir_admin(self) -> dict | None:
        """Devolve o admin ou já responde 401. Use: `if not (a := self._exigir_admin()): return`."""
        admin = self._admin()
        if not admin:
            json_response(self, {
                "ok": False,
                "erro": "Autenticação administrativa exigida para esta rota.",
                "dica": "Faça POST /api/admin/login e envie Authorization: Bearer <token>",
            }, 401)
            return None
        return admin

    def do_OPTIONS(self):
        self.send_response(204)
        if self.server.origem_permitida:
            self.send_header("Access-Control-Allow-Origin", self.server.origem_permitida)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------------- GET ----------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        try:
            if path == "/api/status":
                return json_response(self, {
                    "ok": True,
                    "servico": "Urna Eletrônica",
                    "versao": "4.0-rede",
                    "mensagem": "Servidor online",
                    "tls": False,
                    "aviso": "Canal sem TLS. Use proxy HTTPS ou VPN em produção.",
                })

            if path == "/api/eleicoes":
                status = qs.get("status", [None])[0]
                return json_response(self, {"ok": True, "eleicoes": listar_eleicoes(status)})

            if path.startswith("/api/eleicoes/"):
                partes = path.split("/")
                eid = _inteiro(partes[3] if len(partes) > 3 else None, -1)
                if eid < 0:
                    return json_response(self, {"ok": False, "erro": "ID inválido"}, 400)

                if len(partes) == 4:
                    eleicao = obter_eleicao(eid)
                    if not eleicao:
                        return json_response(self, {"ok": False, "erro": "Eleição não encontrada"}, 404)
                    return json_response(self, {"ok": True, "eleicao": eleicao})

                recurso = partes[4] if len(partes) > 4 else ""

                # --- públicas ---
                if recurso == "candidatos":
                    cargo = qs.get("cargo", [None])[0]
                    return json_response(self, {
                        "ok": True,
                        "cargos": listar_cargos(eid),
                        "candidatos": listar_candidatos(eid, cargo=cargo),
                    })
                if recurso == "cargos":
                    return json_response(self, {"ok": True, "cargos": listar_cargos(eid)})
                if recurso == "compromissos":
                    # Quadro público: só hashes, nenhum dado pessoal
                    return json_response(self, {"ok": True, "quadro": quadro_compromissos(eid)})

                # --- restritas ---
                if recurso == "resultados":
                    if not self._exigir_admin():
                        return
                    estrito = qs.get("estrito", ["1"])[0] != "0"
                    try:
                        return json_response(self, {
                            "ok": True, "resultados": obter_resultados(eid, estrito=estrito)
                        })
                    except ErroApuracao as e:
                        return json_response(self, {
                            "ok": False, "erro": str(e), "falhas": e.detalhes,
                            "codigo": "APURACAO_COMPROMETIDA",
                        }, 409)
                if recurso == "relatorio-votos":
                    if not self._exigir_admin():
                        return
                    return json_response(self, {"ok": True, "relatorio": relatorio_votos(eid)})
                if recurso == "eleitores":
                    if not self._exigir_admin():
                        return
                    return json_response(self, {"ok": True, "eleitores": listar_eleitores(eid)})
                if recurso == "integridade":
                    if not self._exigir_admin():
                        return
                    return json_response(self, {
                        "ok": True, "integridade": auditoria_consistencia_votos(eid)
                    })

            if path == "/api/auditoria":
                if not self._exigir_admin():
                    return
                acao = qs.get("acao", [None])[0]
                busca = qs.get("busca", [None])[0]
                limite = _inteiro(qs.get("limite", ["100"])[0], 100)
                return json_response(self, {
                    "ok": True,
                    "registros": consultar_auditoria(acao=acao, busca=busca, limite=limite),
                    "resumo": listar_acoes_auditoria(),
                })

            return json_response(self, {"ok": False, "erro": "Rota não encontrada"}, 404)

        except Exception as e:
            logger.exception("Erro em GET %s", path)
            return json_response(self, {"ok": False, "erro": str(e)}, 500)

    # ---------------- POST ----------------

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        try:
            dados = ler_json(self)
        except ValueError as e:
            return json_response(self, {"ok": False, "erro": str(e)}, 413)

        try:
            # ---------- administração ----------
            if path == "/api/admin/login":
                usuario = dados.get("usuario", "")
                senha = dados.get("senha", "")
                try:
                    admin = verificar_admin(usuario, senha, origem=self.client_address[0])
                except PermissionError as e:
                    return json_response(self, {"ok": False, "erro": str(e)}, 429)
                if not admin:
                    return json_response(self, {"ok": False, "erro": "Usuário ou senha inválidos"}, 401)
                sessao = criar_sessao_admin(admin["id"], origem=self.client_address[0])
                return json_response(self, {
                    "ok": True,
                    "admin": {"id": admin["id"], "usuario": admin["usuario"], "nome": admin["nome"]},
                    "trocar_senha": admin.get("trocar_senha", False),
                    "sessao": sessao,
                })

            if path == "/api/admin/logout":
                cabecalho = self.headers.get("Authorization", "")
                token = cabecalho[7:].strip() if cabecalho.lower().startswith("bearer ") else ""
                encerrar_sessao_admin(token)
                return json_response(self, {"ok": True, "mensagem": "Sessão encerrada"})

            if path == "/api/admin/senha":
                admin = self._exigir_admin()
                if not admin:
                    return
                try:
                    trocou = alterar_senha_admin(
                        admin["usuario"], dados.get("senha_atual", ""), dados.get("senha_nova", "")
                    )
                except ValueError as e:
                    return json_response(self, {"ok": False, "erro": str(e)}, 400)
                if not trocou:
                    return json_response(self, {"ok": False, "erro": "Senha atual incorreta"}, 403)
                return json_response(self, {
                    "ok": True,
                    "mensagem": "Senha alterada. Faça login novamente.",
                })

            # ---------- conferência pública de recibo ----------
            if path == "/api/recibo/verificar":
                eid = _inteiro(dados.get("eleicao_id"), 0)
                compromisso = (dados.get("compromisso") or "").strip()
                if not eid or not compromisso:
                    return json_response(self, {
                        "ok": False, "erro": "eleicao_id e compromisso são obrigatórios"
                    }, 400)
                return json_response(self, {
                    "ok": True, "verificacao": verificar_recibo(eid, compromisso)
                })

            # ---------- votação remota segura ----------
            # 1) Desafio anti-replay
            if path == "/api/remoto/desafio":
                eid = _inteiro(dados.get("eleicao_id"), 0)
                eleicao = obter_eleicao(eid)
                if not eleicao or eleicao["status"] != "aberta":
                    return json_response(self, {"ok": False, "erro": "Eleição não está aberta"}, 400)
                return json_response(self, {"ok": True, **criar_desafio(eid)})

            # 2) Autentica CPF + desafio → sessão
            if path == "/api/remoto/autenticar":
                eid = _inteiro(dados.get("eleicao_id"), 0)
                cpf = dados.get("cpf", "")
                challenge_id = dados.get("challenge_id", "")
                if not eid or not cpf or not challenge_id:
                    return json_response(self, {
                        "ok": False,
                        "erro": "eleicao_id, cpf e challenge_id são obrigatórios"
                    }, 400)
                if not validar_cpf(cpf):
                    return json_response(self, {"ok": False, "erro": "CPF inválido (algoritmo oficial)"}, 400)

                eleicao = obter_eleicao(eid)
                if not eleicao or eleicao["status"] != "aberta":
                    return json_response(self, {"ok": False, "erro": "Eleição não está aberta"}, 400)

                eleitor = autenticar_eleitor(eid, cpf)
                if not eleitor:
                    return json_response(self, {"ok": False, "erro": "CPF não encontrado nesta eleição"}, 404)

                pendentes = eleitor["cargos_pendentes"]
                if not pendentes:
                    return json_response(self, {
                        "ok": False,
                        "erro": "Este CPF já votou em todos os cargos desta eleição",
                        "ja_votou": True,
                        "cargos_votados": eleitor["cargos_votados"],
                    }, 403)

                try:
                    sess = criar_sessao(eid, eleitor["id"], normalizar_cpf(cpf), challenge_id)
                except ValueError as e:
                    return json_response(self, {"ok": False, "erro": str(e)}, 403)

                return json_response(self, {
                    "ok": True,
                    "sessao": sess,
                    "cargos_pendentes": pendentes,
                    "cargos_votados": eleitor["cargos_votados"],
                    "eleitor": {
                        "id": eleitor["id"],
                        "nome": eleitor["nome"],
                        # Nunca devolve o CPF completo por rota não autenticada
                        "cpf": f"{normalizar_cpf(cpf)[:3]}.***.***-{normalizar_cpf(cpf)[9:]}",
                        "unidade": eleitor.get("unidade"),
                        "bloco": eleitor.get("bloco"),
                        "peso": eleitor.get("peso", 1.0),
                    },
                })

            # 3) Voto: um por cargo, com token de sessão + nonce único
            if path == "/api/remoto/votar":
                eid = _inteiro(dados.get("eleicao_id"), 0)
                eleitor_id = _inteiro(dados.get("eleitor_id"), 0)
                token = dados.get("token", "")
                nonce = dados.get("nonce", "")
                cargo = (dados.get("cargo") or "").strip()
                tipo = dados.get("tipo_voto", "")
                numero = dados.get("numero_candidato")

                eleicao = obter_eleicao(eid)
                if not eleicao or eleicao["status"] != "aberta":
                    return json_response(self, {"ok": False, "erro": "Eleição não está aberta"}, 400)

                try:
                    validar_sessao(token, eid, eleitor_id)
                except ValueError as e:
                    return json_response(self, {"ok": False, "erro": str(e)}, 403)

                if not cargo:
                    return json_response(self, {
                        "ok": False,
                        "erro": "cargo é obrigatório",
                        "cargos_pendentes": cargos_pendentes(eid, eleitor_id),
                    }, 400)

                candidato_id = None
                if tipo == "candidato":
                    if numero is None:
                        return json_response(self, {"ok": False, "erro": "Número do candidato obrigatório"}, 400)
                    cand = obter_candidato_por_numero(eid, _inteiro(numero, -1), cargo=cargo)
                    if not cand:
                        return json_response(self, {
                            "ok": False, "erro": f"Candidato não encontrado para o cargo de {cargo}"
                        }, 404)
                    candidato_id = cand["id"]
                elif tipo not in ("branco", "nulo"):
                    return json_response(self, {"ok": False, "erro": "tipo_voto inválido"}, 400)

                # Queima o nonce (anti-replay) mantendo a sessão viva para
                # os demais cargos. A duplicidade real é barrada em registrar_voto.
                try:
                    consumir_sessao(token, nonce, manter_ativa=True)
                except ValueError as e:
                    return json_response(self, {"ok": False, "erro": str(e)}, 403)

                try:
                    voto_id = registrar_voto(eid, eleitor_id, cargo, tipo, candidato_id)
                except ValueError as e:
                    return json_response(self, {"ok": False, "erro": str(e)}, 409)

                restantes = cargos_pendentes(eid, eleitor_id)
                if not restantes:
                    encerrar_sessao(token)

                recibo = obter_recibo_voto(voto_id) or {}
                return json_response(self, {
                    "ok": True,
                    "mensagem": f"Voto para {cargo} registrado com sucesso",
                    "voto_id": voto_id,
                    "cargo": cargo,
                    "compromisso": recibo.get("compromisso"),
                    "tem_prova_or": recibo.get("tem_prova_or"),
                    "cargos_pendentes": restantes,
                    "concluido": not restantes,
                })

            return json_response(self, {"ok": False, "erro": "Rota não encontrada"}, 404)

        except Exception as e:
            logger.exception("Erro em POST %s", path)
            return json_response(self, {"ok": False, "erro": str(e)}, 500)


def main():
    parser = argparse.ArgumentParser(description="Servidor da Urna Eletrônica")
    parser.add_argument("--host", default="0.0.0.0", help="Interface de rede (padrão: 0.0.0.0)")
    parser.add_argument("--porta", type=int, default=8080, help="Porta TCP (padrão: 8080)")
    parser.add_argument("--db", default=None, help="Caminho do arquivo SQLite (padrão: urna.db)")
    parser.add_argument("--cors", default=None,
                        help="Origem permitida em CORS (ex: https://urna.local). "
                             "Sem isso, nenhum cabeçalho CORS é enviado.")
    args = parser.parse_args()

    # Caminho customizado do banco SQLite
    if args.db:
        os.environ["URNA_DB"] = os.path.abspath(args.db)
        import database
        database.DB_PATH = os.environ["URNA_DB"]

    from database import DB_PATH, inspecionar_banco, fazer_backup
    inicializar_banco()

    # Backup automático ao iniciar o servidor
    try:
        if os.path.isfile(DB_PATH):
            bk = fazer_backup("inicio_servidor")
            print(f"  Backup ao iniciar: {os.path.basename(bk)}")
    except Exception as e:
        print(f"  (backup inicial não realizado: {e})")

    server = ThreadedHTTPServer((args.host, args.porta), UrnaHandler)
    server.origem_permitida = args.cors

    # Descobre IP local para mostrar nas instruções
    import socket
    ip_local = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_local = s.getsockname()[0]
        s.close()
    except OSError:
        pass

    info = inspecionar_banco()
    print("""
╔══════════════════════════════════════════════════════════════╗
║         URNA ELETRÔNICA — SERVIDOR CENTRAL v4.0              ║
╚══════════════════════════════════════════════════════════════╝
""")
    print(f"  Escutando em:  http://{args.host}:{args.porta}")
    print(f"  IP deste PC:   {ip_local}")
    print(f"  Banco SQLite:  {info['caminho']} ({info['tamanho_kb']} KB)")
    print(f"  Status:        http://{ip_local}:{args.porta}/api/status")
    print()
    print("  Rotas com dados pessoais exigem login administrativo (Bearer token).")
    if args.host == "0.0.0.0":
        print("  ⚠ O canal NÃO é cifrado. Use proxy TLS (Caddy/Nginx) ou VPN.")
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
    logger.info("Servidor iniciado em %s:%s", args.host, args.porta)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor encerrado.\n")
        server.server_close()


if __name__ == "__main__":
    main()
