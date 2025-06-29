# -*- coding: utf-8 -*-
"""
Bot Discord - Metas Academia
Armazena progresso por usuário, salva/atualiza em metas.json no GitHub via API.
"""
import os, json, datetime, base64, requests, traceback
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import defaultdict
import discord
from discord.ext import commands
from dotenv import load_dotenv

# ----- ENV/SETUP -----
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO") # Ex: "TheEye0/botds"
METAS_FILE = os.getenv("METAS_FILE", "metas.json")
PORT = int(os.getenv("PORT", 10000))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ----- Github -----
def metas_url():
    return f"https://api.github.com/repos/{GITHUB_REPO}/contents/{METAS_FILE}"

def carregar_metas():
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(metas_url(), headers=headers, timeout=10)
        if r.ok:
            raw = base64.b64decode(r.json()["content"])
            return json.loads(raw)
    except Exception:
        traceback.print_exc()
    return {}

def salvar_metas(metas: dict):
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    # Pega o SHA do último commit para update
    r = requests.get(metas_url(), headers=headers)
    sha = r.json().get("sha") if r.ok else None
    content = base64.b64encode(json.dumps(metas, ensure_ascii=False, indent=2).encode()).decode()
    data = {
        "message": "update metas.json",
        "content": content,
        "branch": "main"
    }
    if sha: data["sha"] = sha
    try:
        r = requests.put(metas_url(), headers=headers, json=data, timeout=10)
        if not r.ok:
            print(f"Erro ao salvar metas: {r.text}")
    except Exception:
        traceback.print_exc()

# ----- Funções -----
def get_user_meta(metas, uid):
    return metas.get(str(uid))

def meta_status(meta):
    hoje = datetime.date.today()
    fim = datetime.date.fromisoformat(meta["data_final"])
    feito = meta["feito"]
    total = meta["total"]
    dias_restantes = (fim - hoje).days
    status = ""
    if feito >= total:
        status = f"🎉 Parabéns, meta CONCLUÍDA! ({feito}/{total})"
    elif hoje > fim:
        status = f"⏰ Meta ENCERRADA pelo prazo. Você fez {feito}/{total}."
    else:
        status = f"Progresso: {feito} de {total}. Dias restantes: {dias_restantes} (até {meta['data_final']})."
    return status

def remove_meta(metas, uid):
    if str(uid) in metas:
        del metas[str(uid)]

# ----- Commands -----
@bot.command()
async def meta(ctx, total: int, data_final: str):
    """Cadastra ou atualiza meta. Exemplo: !meta 24 2024-08-20"""
    metas = carregar_metas()
    try:
        fim = datetime.date.fromisoformat(data_final)
    except Exception:
        return await ctx.send("❌ Data inválida. Use AAAA-MM-DD.")
    metas[str(ctx.author.id)] = {
        "total": total,
        "feito": 0,
        "data_final": data_final
    }
    salvar_metas(metas)
    await ctx.send(f"✅ Meta registrada: {total} treinos até {data_final}.")

@bot.command()
async def pago(ctx):
    """Registra 1 treino, mostra progresso e apaga meta se concluída ou expirada."""
    metas = carregar_metas()
    uid = str(ctx.author.id)
    meta = get_user_meta(metas, uid)
    if not meta:
        return await ctx.send("Você não tem uma meta cadastrada. Use !meta.")
    meta["feito"] += 1
    hoje = datetime.date.today()
    fim = datetime.date.fromisoformat(meta["data_final"])
    total = meta["total"]
    feito = meta["feito"]
    status = ""
    if feito >= total:
        status = f"🎉 Parabéns! Você completou sua meta: {feito}/{total} treinos!"
        remove_meta(metas, uid)
    elif hoje > fim:
        status = f"⏰ O prazo terminou. Você fez {feito}/{total}. Nova meta? (!meta)"
        remove_meta(metas, uid)
    else:
        dias_restantes = (fim - hoje).days
        status = f"Progresso: {feito}/{total} treinos. Dias restantes: {dias_restantes} (até {meta['data_final']})."
        metas[uid] = meta  # Salva update
    salvar_metas(metas)
    await ctx.send(status)

@bot.command()
async def progresso(ctx):
    """Mostra o progresso atual da sua meta."""
    metas = carregar_metas()
    meta = get_user_meta(metas, ctx.author.id)
    if not meta:
        return await ctx.send("Você não tem meta cadastrada. Use !meta.")
    await ctx.send(meta_status(meta))

@bot.command()
async def resetmeta(ctx):
    """Apaga a sua meta atual."""
    metas = carregar_metas()
    if str(ctx.author.id) in metas:
        remove_meta(metas, ctx.author.id)
        salvar_metas(metas)
        await ctx.send("Meta removida!")
    else:
        await ctx.send("Você não tem meta ativa para remover.")

# ----- Keep-alive HTTP -----
class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot online!")

Thread(target=lambda: HTTPServer(("0.0.0.0", PORT), KeepAliveHandler).serve_forever(), daemon=True).start()

# ----- Bot Ready -----
@bot.event
async def on_ready():
    print(f"Bot online: {bot.user} | Comandos: !meta, !pago, !progresso, !resetmeta")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
