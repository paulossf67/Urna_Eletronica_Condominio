#!/usr/bin/env python3
"""
Terminal de VOTAÇÃO REMOTA SEGURA.

Fluxo:
  1. Pede desafio ao servidor (anti-replay)
  2. Autentica CPF + desafio → token de sessão
  3. Vota UMA vez em cada cargo, cada requisição com nonce próprio

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


def votar_em_cargo(base: str, eid: int, eleitor: dict, token: str,
                   cargo: str, candidatos: list[dict]) -> dict:
    """Conduz a escolha e o envio do voto de um único cargo."""
    do_cargo = [c for c in candidatos if c["cargo"] == cargo]

    while True:
        print(f"\n  ══ CARGO: {cargo.upper()} ══\n")
        for c in do_cargo:
            print(f"  Nº {c['numero']:02d} — {c['nome']}")
            if c.get("descricao"):
                print(f"           {c['descricao']}")
        print("\n  Digite o número, BRANCO, NULO — ou 0 para cancelar a votação")
        escolha = input("  Voto: ").strip().upper()

        if escolha == "0":
            return {"cancelado": True}

        tipo, numero = None, None
        if escolha == "BRANCO":
            tipo = "branco"
        elif escolha == "NULO":
            tipo = "nulo"
        else:
            try:
                numero = int(escolha)
            except ValueError:
                print("  ✗ Entrada inválida.")
                continue
            if not any(c["numero"] == numero for c in do_cargo):
                print(f"  ✗ O número {numero} não é candidato a {cargo}.")
                continue
            tipo = "candidato"

        # Confirmação
        if tipo == "candidato":
            cand = next(c for c in do_cargo if c["numero"] == numero)
            print(f"\n  ┌──────────────────────────────────────┐")
            print(f"  │ Nº {cand['numero']:02d}  {cand['nome'][:28]:<28}│")
            print(f"  │ {cargo[:36]:<36} │")
            print(f"  └──────────────────────────────────────┘")
        else:
            print(f"\n  >>> VOTO {tipo.upper()} para {cargo} <<<")

        if input("\n  Confirma? [1] SIM [2] corrigir: ").strip() != "1":
            continue

        payload = {
            "eleicao_id": eid,
            "eleitor_id": eleitor["id"],
            "token": token,
            "nonce": secrets.token_hex(16),
            "cargo": cargo,
            "tipo_voto": tipo,
        }
        if tipo == "candidato":
            payload["numero_candidato"] = numero

        resp = api(base, "POST", "/api/remoto/votar", payload)
        if resp.get("ok"):
            return resp
        print(f"\n  ✗ {resp.get('erro')}")
        if input("  Tentar novamente neste cargo? [1] SIM [2] NÃO: ").strip() != "1":
            return {"erro": resp.get("erro")}


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
║     Desafio → Sessão HMAC → Um voto por cargo + Nonce        ║
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
            if auth.get("cargos_votados"):
                print(f"    Cargos já votados: {', '.join(auth['cargos_votados'])}")
            pausar()
            continue

        eleitor = auth["eleitor"]
        token = auth["sessao"]["token"]
        pendentes = auth.get("cargos_pendentes") or []

        print(f"\n  ✓ {eleitor['nome']} autenticado")
        if auth.get("cargos_votados"):
            print(f"  Já votou em: {', '.join(auth['cargos_votados'])}")
        print(f"  Cargos a votar agora: {', '.join(pendentes)}")
        print(f"  Sessão segura: {auth['sessao']['ttl']}s")

        resp_c = api(base, "GET", f"/api/eleicoes/{eid}/candidatos")
        candidatos = (resp_c.get("candidatos") or []) if resp_c.get("ok") else []

        recibos = []
        cancelado = False
        for cargo in pendentes:
            r = votar_em_cargo(base, eid, eleitor, token, cargo, candidatos)
            if r.get("cancelado"):
                cancelado = True
                break
            if not r.get("ok"):
                break
            recibos.append((cargo, r.get("compromisso")))
            print(f"\n  ✓ Voto para {cargo} registrado.")

        limpar()
        if cancelado and not recibos:
            print("\n  Votação cancelada. Nenhum voto foi registrado.")
        elif recibos:
            print("""
  ╔══════════════════════════════════════════════╗
  ║       VOTO(S) REGISTRADO(S) COM SUCESSO      ║
  ╚══════════════════════════════════════════════╝
""")
            print("  Recibos (compromissos) — guarde para conferência:\n")
            for cargo, comp in recibos:
                print(f"  {cargo}:")
                print(f"    {comp}\n")
            faltam = [c for c in pendentes if c not in [r[0] for r in recibos]]
            if faltam:
                print(f"  ⚠ Ainda faltam os cargos: {', '.join(faltam)}")
            else:
                print("  Todos os cargos foram votados. Sessão encerrada.")
            print("\n  Confira seu recibo no quadro público com a opção do")
            print("  terminal de relatórios ou em /api/recibo/verificar.")
        pausar()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Encerrado.\n")
