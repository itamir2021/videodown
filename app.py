import subprocess, json, re, os, uuid, threading, time
from flask import Flask, request, jsonify, Response, send_from_directory, send_file
from flask_cors import CORS

app = Flask(__name__, static_folder="static")
CORS(app)

DOWNLOAD_DIR = "/tmp/videodown"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Limpa arquivos com mais de 30 minutos automaticamente
def cleanup_old_files():
    while True:
        time.sleep(600)
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            try:
                if now - os.path.getmtime(fp) > 1800:
                    os.remove(fp)
            except:
                pass

threading.Thread(target=cleanup_old_files, daemon=True).start()

ERRORS = {
    "Sign in to confirm": "YouTube bloqueou temporariamente. Tente novamente em alguns segundos.",
    "Private video": "Este vídeo é privado ou foi removido.",
    "Video unavailable": "Vídeo indisponível ou foi deletado.",
    "Unable to extract": "Link inválido ou site não suportado.",
    "HTTP Error 403": "Acesso negado pelo site.",
    "HTTP Error 429": "Muitas requisições. Aguarde 1 minuto.",
}

def friendly_error(text):
    for key, msg in ERRORS.items():
        if key in text:
            return msg
    return text[:300] if text.strip() else "Erro desconhecido."

def build_cmd(url, fmt, audio_only, playlist, job_id):
    out_tmpl = os.path.join(DOWNLOAD_DIR, f"{job_id}_%(title).60s.%(ext)s")
    cmd = [
        "yt-dlp", "--newline",
        "--retries", "5", "--fragment-retries", "5",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "--add-header", "Accept-Language:pt-BR,pt;q=0.9,en;q=0.8",
        "--extractor-args", "youtube:player_client=web,default",
        "--sleep-interval", "1",
    ]
    if not playlist:
        cmd.append("--no-playlist")
    if audio_only:
        cmd += ["-f", "bestaudio", "-x", "--audio-format", "mp3"]
    else:
        cmd += ["-f", fmt or "bestvideo+bestaudio/best", "--merge-output-format", "mp4"]
    cmd += ["-o", out_tmpl, url]
    return cmd

@app.route("/api/download", methods=["POST"])
def download():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url or not re.match(r"^https?://", url, re.I):
        return jsonify({"ok": False, "error": "URL inválida."}), 400

    fmt        = data.get("fmt", "bestvideo+bestaudio/best")
    audio_only = data.get("audioOnly", False)
    playlist   = data.get("playlist", False)
    job_id     = str(uuid.uuid4())[:8]
    cmd        = build_cmd(url, fmt, audio_only, playlist, job_id)

    def stream():
        all_output = []
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, encoding="utf-8", errors="replace"
            )
            pct_re = re.compile(r"(\d+\.?\d*)%")
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                all_output.append(line)
                m = pct_re.search(line)
                if m:
                    yield f"data: {json.dumps({'type':'progress','pct':float(m.group(1)),'msg':line})}\n\n"
                else:
                    yield f"data: {json.dumps({'type':'log','msg':line})}\n\n"
            proc.wait()
            if proc.returncode == 0:
                # Achar o arquivo gerado
                files = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(job_id)]
                if files:
                    fname = files[0]
                    yield f"data: {json.dumps({'type':'done','file':fname,'download_url':f'/api/file/{fname}'})}\n\n"
                else:
                    yield f"data: {json.dumps({'type':'error','msg':'Arquivo não encontrado após download.'})}\n\n"
            else:
                full = "\n".join(all_output)
                yield f"data: {json.dumps({'type':'error','msg':friendly_error(full)})}\n\n"
        except FileNotFoundError:
            yield f"data: {json.dumps({'type':'error','msg':'yt-dlp não encontrado no servidor.'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','msg':str(e)})}\n\n"

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/file/<filename>")
def serve_file(filename):
    # Segurança: só serve arquivos do diretório correto
    safe = re.sub(r"[^\w\.\-]", "", filename)
    fp = os.path.join(DOWNLOAD_DIR, safe)
    if not os.path.exists(fp):
        return jsonify({"error": "Arquivo não encontrado"}), 404
    return send_file(fp, as_attachment=True, download_name=safe.split("_", 1)[-1] if "_" in safe else safe)

@app.route("/api/health")
def health():
    try:
        v = subprocess.check_output(["yt-dlp","--version"], text=True).strip()
        return jsonify({"ok": True, "ytdlp": v})
    except:
        return jsonify({"ok": False, "ytdlp": None})

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve(path):
    f = os.path.join(app.static_folder, path)
    if path and os.path.exists(f):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, "index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n🎬 VideoDown Online rodando na porta {port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
