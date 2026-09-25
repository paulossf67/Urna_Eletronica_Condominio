#!/usr/bin/env python3
"""
Terminal de VOTAÇÃO REMOTA SEGURA.

Fluxo:
  1. Pede desafio ao servidor (anti-replay)
  2. Autentica CPF + desafio → token de sessão (5 min, 1 voto)
  3. Envia voto com token + nonce único

Uso:
  python3 cliente_votacao.py --servidor 192.168.1.10
"""

import argparse
import json
import os
import secrets
import sys
import urllib.request
import urllib.error


def limpar():
    os.system("cls" if os.name == "nt" else "clear")


def pausar():
    input("\n  Pressione ENTER para continuar...")


def api(base: str, metodo: str, path: str, dados: dict | None = None) -> dict:
    url = base.rstrip("/") + path
    body = None
    headers = {"Accept": "application/json"}
    if dados is not None:
        body = json.dumps(dados).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "erro": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "erro": f"Falha de conexão: {e}"}


def main():
    parser = argparse.ArgumentParser(description="Votação Remota Segura — Urna Eletrônica")
    parser.add_argument("--servidor", default="127.0.0.1", help="IP do servidor")
    parser.add_argument("--porta", type=int, default=8080, help="Porta")
    args = parser.parse_args()
    base = f"http://{args.servidor}:{args.porta}"

    status = api(base, "GET", "/api/status")
    if not status.get("ok"):
        print(f"\n  ✗ Não conectou a {base}")
        print(f"    {status.get('erro', '')}\n")
        print("  Dica: use HTTPS/VPN em produção (veja README).\n")
        sys.exit(1)

    while True:
        limpar()
        print("""
╔══════════════════════════════════════════════════════════════╗
║     VOTAÇÃO REMOTA SEGURA                                    ║
║     Desafio → Sessão HMAC → Voto + Nonce                     ║
╚══════════════════════════════════════════════════════════════╝
""")
        print(f"  Servidor: {base}\n")

        resp = api(base, "GET", "/api/eleicoes?status=aberta")
        if not resp.get("ok"):
            print(f"  ✗ {resp.get('erro')}")
            pausar()
            continue

        eleicoes = resp.get("eleicoes") or []
        if not eleicoes:
            print("  Nenhuma eleição aberta.")
            print("  [0] Sair  |  [Enter] Atualizar")
            if input("\n  > ").strip() == "0":
                break
            continue

        print("  Eleições abertas:\n")
        for e in eleicoes:
            print(f"  [{e['id']}] {e['nome']}")

        try:
            eid = int(input("\n  ID da eleição (0=sair): ").strip())
        except ValueError:
            continue
        if eid == 0:
            break

        des = api(base, "POST", "/api/remoto/desafio", {"eleicao_id": eid})
        if not des.get("ok"):
            print(f"\n  ✗ Desafio: {des.get('erro')}")
            pausar()
            continue
        challenge_id = des["challenge_id"]
        print(f"\n  Desafio obtido (válido {des.get('ttl')}s)")

        cpf = input("  CPF: ").strip()
        if not cpf:
            continue

        auth = api(base, "POST", "/api/remoto/autenticar", {
            "eleicao_id": eid,
            "cpf": cpf,
            "challenge_id": challenge_id,
        })
        if not auth.get("ok"):
            print(f"\n  ✗ {auth.get('erro')}")
            pausar()
            continue

        eleitor = auth["eleitor"]
        sessao = auth["sessao"]
        token = sessao["token"]
        print(f"\n  ✓ {eleitor['nome']} autenticado")
        print(f"  Sessão segura: {sessao['ttl']}s para concluir o voto")

        resp_c = api(base, "GET", f"/api/eleicoes/{eid}/candidatos")
        candidatos = (resp_c.get("candidatos") or []) if resp_c.get("ok") else []

        while True:
            print("\n  ── CANDIDATOS ──\n")
            cargo_atual = None
            for c in candidatos:
                if c["cargo"] != cargo_atual:
                    cargo_atual = c["cargo"]
                    print(f"  ── {cargo_atual} ──")
                print(f"  Nº {c['numero']:02d} — {c['nome']}")

            print("\n  Número, BRANCO, NULO ou 0 para cancelar")
            escolha = input("  Voto: ").strip().upper()
            if escolha == "0":
                break

            tipo = numero = None
            if escolha == "BRANCO":
                tipo = "branco"
            elif escolha == "NULO":
                tipo = "nulo"
            else:
                try:
                    numero = int(escolha)
                    tipo = "candidato"
                except ValueError:
                    print("  Inválido.")
                    continue

            conf = input("  Confirma? [1] SIM [2] NÃO: ").strip()
            if conf != "1":
                continue

            nonce = secrets.token_hex(16)
            payload = {
                "eleicao_id": eid,
                "eleitor_id": eleitor["id"],
                "token": token,
                "nonce": nonce,
                "tipo_voto": tipo,
                "cpf": cpf,
            }
            if tipo == "candidato":
                payload["numero_candidato"] = numero

            resp_v = api(base, "POST", "/api/remoto/votar", payload)
            if resp_v.get("ok"):
                limpar()
                print("""
  ╔══════════════════════════════════════════╗
  ║   VOTO REMOTO REGISTRADO COM SUCESSO     ║
  ╚══════════════════════════════════════════╝
""")
                if resp_v.get("compromisso"):
                    print("  Recibo (compromisso) — guarde:")
                    print(f"\n  {resp_v['compromisso']}\n")
                print("  A sessão foi consumida (não reutilizável).")
            else:
                print(f"\n  ✗ {resp_v.get('erro')}")
            pausar()
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Encerrado.\n")
