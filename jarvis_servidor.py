#!/usr/bin/env python3
"""
Servidor local do Jarvis 2: roda só no seu computador (127.0.0.1) e junta três coisas.

  Cérebro  Claude Code na SUA assinatura, sem chave de API, em modo híbrido: o Haiku responde
           o que é simples, o Sonnet o que é complexo e o Opus o que pede raciocínio profundo,
           com a conversa compartilhada entre eles. Se o Opus não responder, o Sonnet assume.
           Tem busca na web e, se você quiser, memória (MCP) e o cofre do Obsidian.
  Skills   Lista as skills instaladas no seu Claude Code (cada uma vira uma estrela na galáxia).
  Voz      Kokoro-82M, gratuito: mistura pm_alex + bm_fable (voz de mago), em ritmo normal.
  Memória  As notas que você pede para o Jarvis anotar viram arquivos Markdown, de
           preferência dentro do seu cofre do Obsidian (pasta "Jarvis").

Requisitos: Python 3.10 a 3.12, Claude Code instalado e com login feito (rode `claude` uma vez).
Voz: pip install kokoro   (sem o Kokoro, o Jarvis usa a voz do navegador)

Uso:
    python jarvis_servidor.py --abrir                 abre o Jarvis em http://127.0.0.1:8880
    python jarvis_servidor.py --abrir --cofre "C:\\Users\\voce\\Documents\\MeuCofre"
    python jarvis_servidor.py --sem-voz            não carrega o Kokoro
    python jarvis_servidor.py --teste              voz sintética, só para testar a ligação

Ideias inspiradas no JARVIS 2.0 (ethanplusai/jarvis), reescritas do zero para Windows.
"""
import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
import queue
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request
import wave
import webbrowser
from collections import OrderedDict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSAO = '2.6'
CASA = Path.home() / 'Jarvis'
TAXA = 24000
VOZ_PADRAO = 'pm_alex,pm_alex,bm_fable'             # 2/3 português (Alex) + 1/3 timbre britânico grave (Fable)
VOZES = ['pm_alex,pm_alex,bm_fable', 'pm_alex,bm_fable', 'pm_santa,bm_fable', 'pm_alex,bm_george', 'pm_alex', 'pm_santa', 'bm_fable']
VOZ_RE = re.compile(r'^[a-z]{2}_[a-z]{2,20}$')
CLAUDE_DIR = Path(os.environ.get('JARVIS_CLAUDE_DIR') or (Path.home() / '.claude'))
ROTACAO = 30                        # depois de tantos turnos, começa uma conversa nova (contexto enxuto)
CABECALHO = ('Você é o Jarvis, assistente de voz pessoal do usuário. Siga à risca o CLAUDE.md desta pasta: '
             'personalidade, estilo de fala e o cabeçalho [[tema:...|nos:...]] obrigatório no início de cada resposta. '
             'Tudo o que você escreve é falado em voz alta, em português do Brasil, sem markdown.')


def log(msg):
    print(time.strftime('%H:%M:%S ') + msg, flush=True)


def slug(texto, limite=50):
    t = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode().lower()
    t = re.sub(r'[^a-z0-9]+', '-', t).strip('-')
    return (t[:limite].rstrip('-')) or 'nota'


# ───────────────────────── voz (Kokoro) ─────────────────────────
ONNX_DIR = CASA / 'kokoro-onnx'
ONNX_BASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/'
# fp16: tão rápido quanto o fp32 no processador, com metade do tamanho. O int8 ficou de fora de propósito:
# no processador ele é umas 6 vezes mais lento (as convoluções quantizadas não têm versão otimizada).
ONNX_MODELOS = {'fp16': 'kokoro-v1.0.fp16.onnx', 'fp32': 'kokoro-v1.0.onnx'}
ONNX_VOZES = 'voices-v1.0.bin'


def baixar(url, destino, andamento=None):
    """Baixa um arquivo grande mostrando o andamento na janela preta (e na página, por andamento)."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(destino.name + '.parcial')
    with urllib.request.urlopen(url, timeout=60) as r, open(parcial, 'wb') as f:
        total, feito, aviso = int(r.headers.get('Content-Length') or 0), 0, 0
        while True:
            bloco = r.read(1 << 20)
            if not bloco:
                break
            f.write(bloco)
            feito += len(bloco)
            if total and andamento:
                andamento(feito * 100 // total)
            if total and feito >= aviso:
                log('baixando %s: %d%%' % (destino.name, feito * 100 // total))
                aviso += total // 5
    if total and parcial.stat().st_size != total:
        raise IOError('download incompleto de %s' % destino.name)
    parcial.replace(destino)


class Voz:
    """Voz de mago. Usa o Kokoro em ONNX (bem mais leve no processador) e, se ele não estiver instalado,
    o Kokoro em PyTorch de antes. Tudo roda no processador; nada precisa de placa de vídeo."""

    def __init__(self, modo, modelo_onnx='fp16'):
        self.modo = modo                # 'kokoro', 'teste' ou None
        self.motor = None               # 'onnx' ou 'torch'
        self.trava = threading.Lock()
        self.pipe = self.onnx = None
        self.estilos = {}
        self.cache = OrderedDict()      # frases curtas que se repetem saem na hora
        self.preparando = False         # baixando ou ligando a voz rápida (ONNX); a página mostra o andamento
        self.progresso = 0
        self.erro_rapida = None
        self.arquivo_onnx = ONNX_DIR / ONNX_MODELOS.get(modelo_onnx, ONNX_MODELOS['fp16'])
        if modo != 'kokoro':
            return
        if importlib.util.find_spec('kokoro_onnx') is None:
            return self._carregar_torch()                       # sem o kokoro-onnx: o Kokoro de antes (ImportError se nenhum)
        if self.arquivo_onnx.exists() and (ONNX_DIR / ONNX_VOZES).exists():
            try:
                return self._carregar_onnx()
            except Exception as e:
                self.erro_rapida = str(e)[:160]
                log('a voz ONNX falhou (%s); tentando o Kokoro de antes' % e)
                return self._reserva()
        # primeira vez: baixa o modelo em segundo plano. O PyTorch nem é carregado (pesado e lento neste caso);
        # enquanto isso a página usa a voz do navegador e mostra o andamento no painel.
        log('preparando a voz de mago rápida (Kokoro ONNX): na primeira vez baixa uns 190 MB...')
        self.preparando = True
        threading.Thread(target=self._baixar_e_trocar, daemon=True).start()

    def _reserva(self):
        """Se a voz rápida não der certo, usa o Kokoro de antes (PyTorch), se ele estiver instalado."""
        try:
            self._carregar_torch()
        except ImportError:
            pass
        except Exception as e:
            log('o Kokoro de antes também falhou (%s)' % e)

    def pronta(self):
        return self.modo == 'teste' or self.motor is not None

    def _carregar_torch(self):
        import warnings
        warnings.filterwarnings('ignore')
        from kokoro import KPipeline
        pipe = KPipeline(lang_code='p', repo_id='hexgrad/Kokoro-82M')   # pronúncia em português
        with self.trava:
            self.pipe, self.motor = pipe, 'torch'
        log('voz de mago: Kokoro (PyTorch)')

    def _carregar_onnx(self):
        from kokoro_onnx import Kokoro
        k = Kokoro(str(self.arquivo_onnx), str(ONNX_DIR / ONNX_VOZES))
        estilos = {}
        k.create('Pronto.', voice=self._estilo(k, VOZ_PADRAO, estilos), speed=1.0, lang='pt-br')   # aquece
        with self.trava:
            self.onnx, self.motor, self.estilos, self.pipe = k, 'onnx', estilos, None
            self.cache.clear()
        log('voz de mago: Kokoro ONNX (%s), mais leve para o processador' % self.arquivo_onnx.name)

    def _baixar_e_trocar(self):
        try:
            for tentativa, pausa in enumerate((0, 5, 20)):     # internet instável: tenta mais duas vezes
                time.sleep(pausa)
                try:
                    base = 0
                    for nome, destino, peso in ((self.arquivo_onnx.name, self.arquivo_onnx, 84), (ONNX_VOZES, ONNX_DIR / ONNX_VOZES, 14)):
                        if not destino.exists():
                            baixar(ONNX_BASE + nome, destino, lambda p, b=base, w=peso: setattr(self, 'progresso', b + p * w // 100))
                        base += peso
                        self.progresso = base
                    break
                except Exception as e:
                    if tentativa == 2:
                        raise
                    log('o download da voz rápida falhou (%s); tentando de novo...' % e)
            self._carregar_onnx()
            self.progresso = 100
        except Exception as e:
            self.erro_rapida = str(e)[:160]
            log('não consegui preparar a voz de mago rápida (%s)' % e)
            if not self.motor:
                self._reserva()
        finally:
            self.preparando = False

    @staticmethod
    def _estilo(k, voz, cache):
        if voz not in cache:
            estilos = [k.get_voice_style(n) for n in voz.split(',')]
            cache[voz] = estilos[0] if len(estilos) == 1 else sum(estilos) / len(estilos)   # mistura de vozes
        return cache[voz]

    def falar(self, texto, voz, velocidade):
        if self.modo == 'teste':
            return self._sintetico(texto)
        chave = (texto, voz, round(velocidade, 2))
        with self.trava:
            if chave in self.cache:
                self.cache.move_to_end(chave)
                return self.cache[chave]
            if self.motor == 'onnx':
                audio = self._falar_onnx(texto, voz, velocidade)
            elif self.motor == 'torch':
                audio = self._falar_torch(texto, voz, velocidade)
            else:
                raise RuntimeError('a voz de mago ainda está sendo preparada')
            if len(texto) <= 160:
                self.cache[chave] = audio
                if len(self.cache) > 80:
                    self.cache.popitem(last=False)
        return audio

    def _falar_onnx(self, texto, voz, velocidade):
        try:
            estilo = self._estilo(self.onnx, voz, self.estilos)
        except Exception:
            estilo = self._estilo(self.onnx, VOZ_PADRAO, self.estilos)
        amostras, _ = self.onnx.create(texto, voice=estilo, speed=float(min(2.0, max(0.5, velocidade))), lang='pt-br')
        return amostras

    def _falar_torch(self, texto, voz, velocidade):
        import numpy as np
        try:
            partes = self._gerar(texto, voz, velocidade)
        except Exception:
            primeira = voz.split(',')[0]
            if primeira == voz:
                raise
            partes = self._gerar(texto, primeira, velocidade)
        if not partes:
            raise RuntimeError('o Kokoro não gerou áudio para esse texto')
        return np.concatenate(partes)

    def _gerar(self, texto, voz, velocidade):
        saida = []
        for _, _, audio in self.pipe(texto, voice=voz, speed=velocidade):
            if audio is not None:
                saida.append(audio.detach().cpu().numpy() if hasattr(audio, 'detach') else audio)
        return saida

    @staticmethod
    def _sintetico(texto):
        n = int(TAXA * min(4.0, 0.3 + len(texto) * 0.04))
        out = []
        for i in range(n):
            t = i / TAXA
            env = min(1.0, t / 0.08, (n / TAXA - t) / 0.12)
            tom = math.sin(2 * math.pi * 105 * t) + 0.5 * math.sin(2 * math.pi * 210 * t)
            out.append(0.3 * env * (0.55 + 0.45 * math.sin(2 * math.pi * 4.2 * t)) * tom / 1.5)
        return out


class FilaEmOrdem:
    """Trava que respeita a ordem de chegada: a primeira frase pedida é a primeira gerada."""

    def __init__(self):
        self.cond = threading.Condition()
        self.proximo = 0
        self.vez = 0

    def entrar(self):
        with self.cond:
            minha = self.proximo
            self.proximo += 1
            while minha != self.vez:
                self.cond.wait()

    def sair(self):
        with self.cond:
            self.vez += 1
            self.cond.notify_all()


FILA_VOZ = FilaEmOrdem()


def wav_bytes(audio):
    if hasattr(audio, 'dtype'):
        import numpy as np
        pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767).astype('<i2').tobytes()
    else:
        pcm = b''.join(struct.pack('<h', int(max(-1.0, min(1.0, x)) * 32767)) for x in audio)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAXA)
        w.writeframes(pcm)
    return buf.getvalue()


# ───────────────────────── cérebro (Claude Code na assinatura) ─────────────────────────
class ErroCerebro(Exception):
    def __init__(self, msg, tipo='erro'):
        super().__init__(msg)
        self.tipo = tipo


class Cerebro:
    def __init__(self, comando, pasta, modelo, mcp, esforco=None):
        self.esforco = esforco
        self.cmd = comando
        self.pasta = pasta
        self.modelo = modelo
        self.mcp = mcp
        self.proc = None
        self.fila = None
        self.persona_hash = None
        self.turnos = 0
        self.trava = threading.Lock()
        self.stderr = []
        self.limite_s = 180
        self.geracao = 0                                       # muda a cada processo novo (a memória da conversa some)

    @staticmethod
    def localizar(caminho=None):
        alvo = caminho or shutil.which('claude')
        if not alvo:                                           # PATH desatualizado: procura nos lugares de instalação padrão
            casa = Path.home()
            local = Path(os.environ.get('LOCALAPPDATA') or casa / 'AppData' / 'Local')
            roaming = Path(os.environ.get('APPDATA') or casa / 'AppData' / 'Roaming')
            for c in (casa / '.local' / 'bin' / 'claude.exe', casa / '.local' / 'bin' / 'claude',
                      local / 'Microsoft' / 'WinGet' / 'Links' / 'claude.exe', roaming / 'npm' / 'claude.cmd'):
                if c.exists():
                    alvo = str(c)
                    break
        if not alvo:
            return None
        if alvo.lower().endswith('.py'):
            return [sys.executable, alvo]                      # Claude simulado, usado nos testes
        if os.name == 'nt' and alvo.lower().endswith(('.cmd', '.bat')):
            return ['cmd', '/c', alvo]
        return [alvo]

    def _parar(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], capture_output=True)
                else:
                    p.terminate()
            except Exception:
                pass

    def _iniciar(self, persona):
        self._parar()
        self.pasta.mkdir(parents=True, exist_ok=True)
        (self.pasta / 'CLAUDE.md').write_text(persona, encoding='utf-8')
        args = self.cmd + [
            '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
            '--include-partial-messages', '--model', self.modelo,
            '--tools', 'WebSearch,WebFetch',                    # só pesquisa: nada de comandos nem edição de arquivos por voz
            '--setting-sources', 'project', '--strict-mcp-config',
            '--dangerously-skip-permissions',                   # voz não responde a pedidos de permissão; as ferramentas já são restritas
            '--append-system-prompt', CABECALHO,
        ]
        if self.mcp:
            args += ['--mcp-config', str(self.mcp)]
        if self.esforco:
            args += ['--effort', self.esforco]
        env = {k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}   # sempre a assinatura, nunca uma chave
        self.stderr = []
        self.fila = queue.Queue()
        self.proc = subprocess.Popen(args, cwd=str(self.pasta), env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=0x08000000 if os.name == 'nt' else 0)
        proc, fila = self.proc, self.fila
        threading.Thread(target=self._ler_saida, args=(proc, fila), daemon=True).start()
        threading.Thread(target=self._ler_erros, args=(proc,), daemon=True).start()
        self.persona_hash = hashlib.sha1(persona.encode('utf-8')).hexdigest()
        self.turnos = 0
        self.geracao += 1
        log('cérebro iniciado (Claude Code, modelo %s)' % self.modelo)

    @staticmethod
    def _ler_saida(proc, fila):
        for linha in iter(proc.stdout.readline, b''):
            try:
                fila.put(json.loads(linha.decode('utf-8', 'replace')))
            except ValueError:
                pass
        fila.put(None)

    def _ler_erros(self, proc):
        for linha in iter(proc.stderr.readline, b''):
            self.stderr = (self.stderr + [linha.decode('utf-8', 'replace').strip()])[-8:]

    def _motivo(self):
        return ' '.join(x for x in self.stderr if x)[-300:] or 'sem detalhes'

    def aquecer(self, persona):
        with self.trava:
            if self.proc is None:
                self._iniciar(persona)

    def reiniciar(self):
        with self.trava:
            self._parar()
            self.persona_hash = None

    def vai_reiniciar(self, persona):
        novo = hashlib.sha1(persona.encode('utf-8')).hexdigest()
        return self.proc is None or self.proc.poll() is not None or novo != self.persona_hash or self.turnos >= ROTACAO

    def turno(self, texto, persona, ao_receber, ao_evento=None):
        with self.trava:
            novo_hash = hashlib.sha1(persona.encode('utf-8')).hexdigest()
            if self.proc is None or self.proc.poll() is not None or novo_hash != self.persona_hash or self.turnos >= ROTACAO:
                self._iniciar(persona)
            while not self.fila.empty():
                self.fila.get_nowait()
            linha = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': texto}}, ensure_ascii=False) + '\n'
            try:
                self.proc.stdin.write(linha.encode('utf-8'))
                self.proc.stdin.flush()
            except OSError:
                self._parar()
                raise ErroCerebro('o Claude Code fechou: ' + self._motivo())
            self.turnos += 1
            parcial = False
            ferramentas = {}
            ouvinte = [True]
            falou = [False]

            def entregar(t):
                if ouvinte[0]:
                    try:
                        ao_receber(t)
                        falou[0] = True
                    except OSError:
                        ouvinte[0] = False          # a página desistiu; o turno termina em silêncio

            def avisar(ev):
                if ouvinte[0] and ao_evento:
                    try:
                        ao_evento(ev)
                    except OSError:
                        ouvinte[0] = False

            limite = time.time() + self.limite_s
            while True:
                resta = limite - time.time()
                if resta <= 0:
                    self._parar()
                    raise ErroCerebro('o Claude Code demorou demais para responder', 'tempo')
                try:
                    ev = self.fila.get(timeout=min(resta, 1.0))
                except queue.Empty:
                    if self.proc is None or self.proc.poll() is not None:
                        raise ErroCerebro('o Claude Code fechou: ' + self._motivo(), self._tipo(self._motivo()))
                    continue
                if ev is None:
                    self.proc = None
                    raise ErroCerebro('o Claude Code fechou: ' + self._motivo(), self._tipo(self._motivo()))
                tipo = ev.get('type')
                if tipo == 'stream_event':
                    e = ev.get('event') or {}
                    d = e.get('delta') or {}
                    if e.get('type') == 'content_block_delta' and d.get('type') == 'text_delta' and d.get('text'):
                        parcial = True
                        entregar(d['text'])
                    elif e.get('type') == 'content_block_start' and falou[0]:
                        entregar('\n')                     # texto depois de uma pesquisa: separa as frases para a voz
                elif tipo == 'assistant':
                    for b in (ev.get('message') or {}).get('content') or []:
                        if not isinstance(b, dict):
                            continue
                        if b.get('type') == 'tool_use':                         # avisa a página: "pesquisando", raio na estrela
                            entrada = b.get('input') if isinstance(b.get('input'), dict) else {}
                            ferramentas[str(b.get('id') or '')] = (str(b.get('name') or ''), entrada)
                            avisar({'t': 'ferramenta', 'nome': str(b.get('name') or ''),
                                    'skill': str(entrada.get('skill') or entrada.get('command') or '')[:80]})
                            if b.get('name') == 'WebFetch' and entrada.get('url'):
                                avisar({'t': 'fontes', 'consulta': 'página consultada', 'links': [{'titulo': str(entrada['url'])[:120], 'url': str(entrada['url'])}]})
                        elif b.get('type') == 'text' and b.get('text') and not parcial:   # Claude Code antigo, sem eventos parciais
                            entregar(b['text'])
                elif tipo == 'user':                                    # resultado de uma pesquisa: as fontes vão para o painel
                    for b in (ev.get('message') or {}).get('content') or []:
                        if isinstance(b, dict) and b.get('type') == 'tool_result':
                            nome, entrada = ferramentas.get(str(b.get('tool_use_id') or ''), ('', {}))
                            if nome == 'WebSearch':
                                links = extrair_links(b.get('content'))
                                if links:
                                    avisar({'t': 'fontes', 'consulta': str(entrada.get('query') or '')[:80], 'links': links})
                elif tipo == 'result':
                    if ev.get('is_error') or (ev.get('subtype') not in (None, 'success')):
                        msg = str(ev.get('result') or ev.get('subtype') or 'erro no Claude Code')
                        tipo = self._tipo(msg)
                        if tipo == 'login':
                            self._parar()                      # depois do login, o próximo turno abre um processo novo
                        raise ErroCerebro(msg, tipo)
                    return

    @staticmethod
    def _tipo(msg):
        return 'login' if re.search(r'login|log in|logged|auth|credential|api key', msg or '', re.I) else 'erro'


def extrair_links(conteudo, limite=5):
    """Títulos e endereços do resultado da pesquisa na web do Claude Code."""
    if isinstance(conteudo, list):
        conteudo = ' '.join(str(x.get('text') or '') if isinstance(x, dict) else str(x) for x in conteudo)
    texto, links, vistos = str(conteudo or ''), [], set()
    for m in re.finditer(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"url"\s*:\s*"((?:[^"\\]|\\.)*)"', texto):
        try:
            titulo, url = json.loads('"%s"' % m.group(1)), json.loads('"%s"' % m.group(2))
        except ValueError:
            continue
        if url.startswith(('http://', 'https://')) and url not in vistos:
            vistos.add(url)
            links.append({'titulo': titulo[:140] or url, 'url': url})
    if not links:                                              # outro formato: pega os endereços soltos
        for url in re.findall(r'https?://[^\s"\'<>\])]+', texto):
            if url not in vistos:
                vistos.add(url)
                links.append({'titulo': re.sub(r'^https?://(www\.)?', '', url).split('/')[0], 'url': url})
    return links[:limite]


NOTICIAS = {'em': 0, 'cartoes': [], 'trava': threading.Lock()}


def buscar_noticias(comando, modelo, cidade, data):
    """Um Claude Code avulso (sem conversa) pesquisa as notícias do dia e devolve cartões para o painel."""
    with NOTICIAS['trava']:
        if NOTICIAS['cartoes'] and time.time() - NOTICIAS['em'] < 20 * 60:
            return NOTICIAS['cartoes']                         # pedidas há pouco: não gasta a assinatura de novo
        regiao = ('para quem mora em %s: duas do Brasil, uma da região de %s' % (cidade, cidade)) if cidade else 'no Brasil: três do país'
        pergunta = ('Hoje é %s. Pesquise na web as notícias mais importantes de hoje %s '
                    'e uma de tecnologia ou inteligência artificial. '
                    'Responda SOMENTE com até 4 linhas, uma por notícia, exatamente no formato '
                    '[[cartao:noticia|título curto|resumo em uma frase|link da fonte]] e nada mais.' % (data, regiao))
        pasta = CASA / 'cerebro' / 'noticias'
        pasta.mkdir(parents=True, exist_ok=True)
        args = comando + ['-p', pergunta, '--model', modelo, '--output-format', 'json', '--tools', 'WebSearch,WebFetch',
                          '--setting-sources', 'project', '--strict-mcp-config', '--dangerously-skip-permissions']
        env = {k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC_')}
        r = subprocess.run(args, cwd=str(pasta), env=env, capture_output=True, timeout=240, stdin=subprocess.DEVNULL,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
        saida = r.stdout.decode('utf-8', 'replace')
        try:
            texto = str(json.loads(saida).get('result') or '')
        except ValueError:
            texto = saida
        cartoes = []
        for m in re.finditer(r'\[\[\s*cart[aã]o\s*:([^\]]+)\]\]', texto):
            partes = [x.strip() for x in m.group(1).split('|')] + ['', '', '', '']
            link = partes[3] if partes[3].startswith(('http://', 'https://')) else ''
            if partes[1]:
                cartoes.append({'titulo': partes[1][:140], 'texto': partes[2][:280], 'link': link})
        if cartoes:
            NOTICIAS.update(em=time.time(), cartoes=cartoes[:6])
        log('notícias do dia: %d encontradas' % len(cartoes))
        return cartoes[:6]


def suporta_esforco(comando):
    try:
        r = subprocess.run(comando + ['--help'], capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
        return b'--effort' in (r.stdout + r.stderr)
    except Exception:
        return False


def nome_modelo(m):
    achado = re.search(r'haiku|sonnet|opus', m or '', re.I)
    return achado.group(0).capitalize() if achado else (m or '?')


class Hibrido:
    """Até três cérebros: rápido (simples), completo (complexo) e máximo (raciocínio profundo),
    com a conversa compartilhada entre eles."""

    def __init__(self, comando, modelos, mcp):
        self.modelos = modelos
        # o rápido não carrega os servidores MCP (memória, Obsidian): abre mais depressa e ocupa menos memória
        self.comando = comando
        self.cerebros = {nivel: Cerebro(comando, CASA / 'cerebro' / nivel, modelo, None if nivel == 'rapido' else mcp)
                         for nivel, modelo in modelos.items()}
        self.uso = time.time()
        if 'maximo' in self.cerebros:
            self.cerebros['maximo'].limite_s = 300            # o Opus pode pensar por mais tempo
        self.hist = []                  # (n, fala do usuário, resposta do Jarvis)
        self.visto = {nivel: 0 for nivel in modelos}
        self.geracao_vista = {nivel: -1 for nivel in modelos}
        self.n = 0
        self.sem_maximo_ate = 0         # o Opus falhou: até quando o Sonnet responde no lugar dele
        self.trava = threading.Lock()

    def aquecer(self):
        for nivel in ('rapido', 'completo'):                   # o máximo só abre quando for usado
            c = self.cerebros.get(nivel)
            if not c:
                continue
            anterior = c.pasta / 'CLAUDE.md'
            if not anterior.exists():
                anterior = CASA / 'cerebro' / 'CLAUDE.md'      # personalidade salva pela versão de um cérebro só
            if anterior.exists():
                c.aquecer(anterior.read_text(encoding='utf-8'))

    def reiniciar(self):
        for c in self.cerebros.values():
            c.reiniciar()
        with self.trava:
            self.hist, self.n = [], 0
            self.visto = {k: 0 for k in self.visto}

    def repousar_se_ocioso(self, minutos):
        """Sem uso por um tempo, fecha os processos do Claude Code e libera a memória do computador."""
        if not minutos or time.time() - self.uso < minutos * 60:
            return
        abertos = [c for c in self.cerebros.values() if c.proc is not None and c.proc.poll() is None]
        if abertos:
            for c in abertos:
                c.reiniciar()
            log('cérebro em repouso: %d min sem uso, memória liberada (acorda quando você chamar o Jarvis)' % minutos)

    def acordar(self):
        self.uso = time.time()
        self.aquecer()

    def turno(self, texto, persona, nivel, ao_receber, ao_evento=None):
        self.uso = time.time()
        nivel = nivel if nivel in self.cerebros else 'completo'
        if nivel == 'maximo' and time.time() < self.sem_maximo_ate:
            nivel = 'completo'                                 # o Opus falhou há pouco: o Sonnet responde
        c = self.cerebros[nivel]
        with self.trava:
            # o outro modelo pode ter respondido as últimas falas: manda um resumo do que este não viu
            novo = c.vai_reiniciar(persona) or c.geracao != self.geracao_vista[nivel]     # processo novo: não lembra de nada
            perdidas = self.hist[-4:] if novo else [h for h in self.hist if h[0] > self.visto[nivel]][-4:]
        enviado = texto
        if perdidas:
            enviado = ('<conversa_recente>\n' + '\n'.join('Usuário: %s\nJarvis: %s' % (u, j) for _, u, j in perdidas)
                       + '\n</conversa_recente>\n' + texto)
        partes = []

        def receber(t):
            partes.append(t)
            ao_receber(t)

        try:
            c.turno(enviado, persona, receber, ao_evento)
        except ErroCerebro as e:
            if nivel != 'maximo' or partes or e.tipo == 'tempo':
                raise
            primeira = self.sem_maximo_ate == 0
            self.sem_maximo_ate = time.time() + 1800
            log('o modelo máximo (%s) não respondeu: %s. O completo assume por 30 minutos.' % (self.modelos['maximo'], str(e).rstrip('. ')))
            if primeira and ao_evento:
                try:
                    ao_evento({'t': 'aviso', 'msg': 'O %s não respondeu agora (talvez não esteja liberado no seu plano, ou o limite dele acabou). '
                                                    'Respondi com o %s e tento o %s de novo daqui a meia hora.'
                                                    % (nome_modelo(self.modelos['maximo']), nome_modelo(self.modelos.get('completo')),
                                                       nome_modelo(self.modelos['maximo']))})
                except OSError:
                    pass
            return self.turno(texto, persona, 'completo', ao_receber, ao_evento)
        with self.trava:
            self.n += 1
            fala = re.sub(r'<contexto>.*?</contexto>\s*', '', texto, flags=re.S)[:600]
            resposta = re.sub(r'\[\[.*?\]\]', '', ''.join(partes)).strip()[:900]
            self.hist = (self.hist + [(self.n, fala, resposta)])[-8:]
            self.visto[nivel] = self.n
            self.geracao_vista[nivel] = c.geracao
        self.uso = time.time()
        log('nível %s (%s)' % (nivel, self.modelos[nivel]))


def _frontmatter(texto):
    meta, chave = {}, None
    m = re.match(r'^\ufeff?---[ \t]*\r?\n(.*?)\r?\n---', texto, re.S)
    for linha in (m.group(1).splitlines() if m else []):
        if linha[:1].isspace() and chave:                     # descrição em várias linhas (> ou |)
            meta[chave] = (meta[chave] + ' ' + linha.strip()).strip()
        elif ':' in linha:
            k, v = linha.split(':', 1)
            chave, v = k.strip().lower(), v.strip()
            meta[chave] = '' if v in ('>', '|', '>-', '|-', '>+', '|+') else v.strip('"\'')
        else:
            chave = None
    return meta


def _ler_json(caminho):
    try:
        return json.loads(Path(caminho).read_text(encoding='utf-8'))
    except Exception:
        return None


def listar_skills():
    """O que o Claude Code reconhece como skill: as pessoais, os comandos (/jarvis) e as dos plugins ativos."""
    achadas, vistas = [], set()

    def ler(arquivo, fonte, padrao, prefixo=''):
        try:
            meta = _frontmatter(arquivo.read_text(encoding='utf-8', errors='replace')[:6000])
        except OSError:
            return
        nome = prefixo + (meta.get('name') or padrao).strip()
        if not nome.strip('/') or nome.lower() in vistas:
            return
        vistas.add(nome.lower())
        achadas.append({'id': 'sk-' + slug(nome, 60), 'nome': nome, 'descricao': (meta.get('description') or '')[:240],
                        'fonte': fonte})

    for f in sorted((CLAUDE_DIR / 'skills').glob('*/SKILL.md')):
        ler(f, 'pessoal', f.parent.name)
    for f in sorted((CLAUDE_DIR / 'commands').glob('*.md')):
        ler(f, 'comando', f.stem, '/')

    ativos = (_ler_json(CLAUDE_DIR / 'settings.json') or {}).get('enabledPlugins') or {}
    mercados = _ler_json(CLAUDE_DIR / 'plugins' / 'known_marketplaces.json') or {}
    instalados = []                                           # (nome@mercado, pasta)

    def coletar(o, chave=None):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == 'installPath' and isinstance(v, str) and chave:
                    instalados.append((chave, Path(v)))
                else:
                    coletar(v, k if '@' in str(k) else chave)
        elif isinstance(o, list):
            for v in o:
                coletar(v, chave)

    coletar(_ler_json(CLAUDE_DIR / 'plugins' / 'installed_plugins.json'))
    if not instalados:                                        # sem o registro: o cache dos plugins (mercado/plugin/versão)
        for pasta in sorted((CLAUDE_DIR / 'plugins' / 'cache').glob('*/*')):
            versoes = sorted((v for v in pasta.iterdir() if v.is_dir()), key=lambda v: v.stat().st_mtime) if pasta.is_dir() else []
            alvo = next((v for v in reversed(versoes) if (v / '.claude-plugin').exists() or (v / 'skills').exists()), pasta)
            instalados.append(('%s@%s' % (pasta.name, pasta.parent.name), alvo))

    for chave, base in instalados:
        if ativos.get(chave) is False:                        # instalado, mas desligado
            continue
        nome_plugin, _, mercado = chave.partition('@')
        lista = None
        raiz_mercado = Path(((mercados.get(mercado) or {}).get('installLocation')) or (CLAUDE_DIR / 'plugins' / 'marketplaces' / mercado))
        for manifesto in (base / '.claude-plugin' / 'marketplace.json', raiz_mercado / '.claude-plugin' / 'marketplace.json'):
            dados = _ler_json(manifesto) or {}
            for p in dados.get('plugins') or []:
                if isinstance(p, dict) and p.get('name') == nome_plugin and isinstance(p.get('skills'), list):
                    lista = [(base / s) if (base / s).exists() else (raiz_mercado / s) for s in p['skills'] if isinstance(s, str)]
            if lista is not None:
                break
        if lista is None:                                     # o padrão: a pasta skills/ do plugin (e as extras do plugin.json)
            extra = (_ler_json(base / '.claude-plugin' / 'plugin.json') or {}).get('skills') or []
            lista = sorted(p.parent for p in base.glob('skills/*/SKILL.md'))
            for s in ([extra] if isinstance(extra, str) else extra if isinstance(extra, list) else []):
                d = base / s
                lista += [d] if (d / 'SKILL.md').exists() else sorted(p.parent for p in d.glob('*/SKILL.md'))
        for pasta in lista:
            if (pasta / 'SKILL.md').exists():
                ler(pasta / 'SKILL.md', nome_plugin, pasta.name)
    return achadas[:200]


def gerar_mcp(cofre):
    """Servidores que o cérebro pode usar. Se existir ~/Jarvis/conexoes.json, ele manda."""
    proprio = CASA / 'conexoes.json'
    if proprio.exists():
        return proprio
    if not shutil.which('npx'):
        return None
    npx = ['cmd', '/c', 'npx'] if os.name == 'nt' else ['npx']
    servidores = {'memory': {'command': npx[0], 'args': npx[1:] + ['-y', '@modelcontextprotocol/server-memory'],
                             'env': {'MEMORY_FILE_PATH': str(CASA / 'memoria-grafo.json')}}}
    if cofre:
        servidores['obsidian'] = {'command': npx[0], 'args': npx[1:] + ['-y', 'obsidian-mcp', str(cofre)]}
    caminho = CASA / 'cerebro' / 'mcp.json'
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps({'mcpServers': servidores}, ensure_ascii=False, indent=2), encoding='utf-8')
    return caminho


# ───────────────────────── memória em Markdown ─────────────────────────
class Notas:
    def __init__(self, pasta):
        self.pasta = pasta
        self.pasta.mkdir(parents=True, exist_ok=True)
        self.trava = threading.Lock()

    def listar(self):
        notas = []
        for f in sorted(self.pasta.glob('*.md'), key=lambda p: p.stat().st_mtime):
            try:
                notas.append(self._ler(f))
            except Exception:
                pass
        return notas

    def _ler(self, f):
        bruto = f.read_text(encoding='utf-8', errors='replace')
        meta, corpo = {}, bruto
        m = re.match(r'^---\s*\n(.*?)\n---\s*\n?', bruto, re.S)
        if m:
            corpo = bruto[m.end():]
            for linha in m.group(1).splitlines():
                if ':' in linha:
                    k, v = linha.split(':', 1)
                    meta[k.strip().lower()] = v.strip()
        titulo = f.stem
        h = re.search(r'^#\s+(.+)$', corpo, re.M)
        if h:
            titulo = h.group(1).strip()
            corpo = corpo[:h.start()] + corpo[h.end():]
        texto = corpo.strip()
        conexoes = [c.strip().strip('"\'') for c in meta.get('conexoes', '').strip('[]').split(',') if c.strip()]
        conexoes += [w.strip() for w in re.findall(r'\[\[([^\]|#]+)', texto)]       # [[links]] do Obsidian
        return {'id': 'n-' + slug(f.stem), 'arquivo': f.name, 'area': meta.get('area', ''), 'titulo': titulo,
                'texto': texto, 'conexoes': conexoes, 'criado': meta.get('criado', ''),
                'editavel': meta.get('origem', '') == 'jarvis'}

    def criar(self, dados):
        titulo = str(dados.get('titulo') or 'Nota').strip()[:120]
        texto = str(dados.get('texto') or '').strip()[:5000]
        area = re.sub(r'[^a-z]', '', str(dados.get('area') or ''))[:20]
        conexoes = [slug(str(c), 64) if not str(c).startswith('n-') else str(c) for c in (dados.get('conexoes') or [])][:6]
        conexoes = [c for c in conexoes if re.match(r'^[a-z0-9-]{1,70}$', c)]
        with self.trava:
            base = slug(titulo)
            f = self.pasta / (base + '.md')
            i = 2
            while f.exists():
                f = self.pasta / ('%s-%d.md' % (base, i))
                i += 1
            conteudo = ('---\narea: %s\ncriado: %s\nconexoes: [%s]\norigem: jarvis\n---\n# %s\n\n%s\n'
                        % (area, datetime.now().strftime('%Y-%m-%dT%H:%M'), ', '.join(conexoes), titulo, texto))
            f.write_text(conteudo, encoding='utf-8')
        return self._ler(f)

    def apagar(self, nota_id):
        with self.trava:
            for f in self.pasta.glob('*.md'):
                if 'n-' + slug(f.stem) == nota_id:
                    if not self._ler(f)['editavel']:
                        return 'esta nota não foi criada pelo Jarvis; edite no Obsidian'
                    f.unlink()
                    return ''
        return 'nota não encontrada'


# ───────────────────────── HTTP ─────────────────────────
class Handler(BaseHTTPRequestHandler):
    voz = None
    cerebro = None
    notas = None
    server_version = 'Jarvis/' + VERSAO

    def _cors(self):
        if not self._origem_ok():
            return
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.send_header('Access-Control-Expose-Headers', 'X-Sintese')

    def _json(self, code, obj):
        corpo = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self._cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _corpo(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 200000:
            raise ValueError('pedido grande demais')
        return json.loads(self.rfile.read(n) or b'{}')

    def _origem_ok(self):
        # só aceita a página do Jarvis aberta como arquivo (origem "null") ou o próprio computador
        o = self.headers.get('Origin')
        return o in (None, 'null') or re.match(r'^https?://(localhost|127\.0\.0\.1)(:\d+)?$', o or '') is not None

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if not self._origem_ok():
            return self._json(403, {'erro': 'origem não permitida'})
        rota = self.path.split('?')[0].rstrip('/')
        if rota in ('', '/jarvis.html', '/index.html'):          # a própria página: mesma origem, sem bloqueios
            pagina = Path(__file__).resolve().parent / 'jarvis.html'
            if not pagina.exists():
                return self._json(404, {'erro': 'coloque o jarvis.html na mesma pasta do jarvis_servidor.py'})
            corpo = pagina.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return
        if rota == '/saude':
            pronta = bool(self.voz and self.voz.pronta())
            return self._json(200, {'ok': True, 'versao': VERSAO, 'voz': self.voz.modo if pronta else None,
                                    'motor': self.voz.modo if pronta else None, 'motor_voz': self.voz.motor if self.voz else None,
                                    'voz_preparando': bool(self.voz and not pronta and self.voz.preparando),
                                    'voz_padrao': VOZ_PADRAO, 'vozes': VOZES,
                                    'voz_rapida_preparando': bool(self.voz and self.voz.preparando),
                                    'voz_progresso': self.voz.progresso if self.voz else 0,
                                    'voz_rapida_erro': self.voz.erro_rapida if self.voz else None,
                                    'cerebro': self.cerebro is not None, 'hibrido': self.cerebro.modelos if self.cerebro else None,
                                    'notas': str(self.notas.pasta)})
        if rota == '/skills':
            return self._json(200, listar_skills())
        if rota == '/notas':
            return self._json(200, self.notas.listar())
        self._json(404, {'erro': 'rota não encontrada'})

    def do_DELETE(self):
        if not self._origem_ok():
            return self._json(403, {'erro': 'origem não permitida'})
        m = re.match(r'^/notas/([a-z0-9-]{1,80})$', self.path.split('?')[0])
        if not m:
            return self._json(404, {'erro': 'rota não encontrada'})
        erro = self.notas.apagar(m.group(1))
        self._json(400 if erro else 200, {'erro': erro} if erro else {'ok': True})

    def do_POST(self):
        if not self._origem_ok():
            return self._json(403, {'erro': 'origem não permitida'})
        rota = self.path.split('?')[0].rstrip('/')
        try:
            dados = self._corpo()
        except Exception as e:
            return self._json(400, {'erro': str(e)})
        if rota == '/tts':
            return self._tts(dados)
        if rota == '/pensar':
            return self._pensar(dados)
        if rota == '/notas':
            return self._json(200, self.notas.criar(dados))
        if rota == '/desligar':                                # um Jarvis novo pedindo a vez (atualização)
            self._json(200, {'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if rota == '/reiniciar':
            if self.cerebro:
                self.cerebro.reiniciar()
            return self._json(200, {'ok': True})
        if rota == '/noticias':
            if not self.cerebro:
                return self._json(503, {'erro': 'Claude Code não encontrado'})
            try:
                cartoes = buscar_noticias(self.cerebro.comando, self.cerebro.modelos.get('rapido', 'haiku'),
                                          str(dados.get('cidade') or '')[:80], str(dados.get('data') or '')[:80])
                return self._json(200, {'cartoes': cartoes})
            except Exception as e:
                log('erro nas notícias: %s' % e)
                return self._json(500, {'erro': str(e)})
        if rota == '/aquecer':                                 # a página ouviu "Jarvis": acorda o cérebro enquanto você fala
            if self.cerebro:
                threading.Thread(target=self.cerebro.acordar, daemon=True).start()
            return self._json(200, {'ok': True})
        self._json(404, {'erro': 'rota não encontrada'})

    def _cliente_saiu(self):
        try:
            pronto, _, _ = select.select([self.connection], [], [], 0)
            return bool(pronto) and self.connection.recv(1, socket.MSG_PEEK) == b''
        except Exception:
            return False

    def _tts(self, dados):
        if not self.voz:
            return self._json(503, {'erro': 'voz desligada'})
        try:
            texto = str(dados.get('texto', '')).strip()[:1200]
            voz = str(dados.get('voz') or VOZ_PADRAO).strip()
            velocidade = min(1.6, max(0.5, float(dados.get('velocidade', 0.9))))
            nomes = voz.split(',')
            if not texto:
                return self._json(400, {'erro': 'texto vazio'})
            if len(nomes) > 4 or not all(VOZ_RE.match(v) for v in nomes):
                return self._json(400, {'erro': 'voz inválida'})
            FILA_VOZ.entrar()                                  # uma frase de cada vez, na ordem em que a página pediu
            try:
                if self._cliente_saiu():                       # a resposta foi interrompida: não gasta processador à toa
                    return
                inicio = time.time()
                audio = self.voz.falar(texto, voz, velocidade)
                gasto = time.time() - inicio
            finally:
                FILA_VOZ.sair()
            corpo = wav_bytes(audio)
            self.send_response(200)
            self._cors()
            self.send_header('X-Sintese', '%.3f' % gasto)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        except Exception as e:
            log('erro na voz: %s' % e)
            self._json(500, {'erro': str(e)})

    def _pensar(self, dados):
        if not self.cerebro:
            return self._json(503, {'erro': 'Claude Code não encontrado', 'tipo': 'sem-claude'})
        texto = str(dados.get('texto', '')).strip()[:8000]
        persona = str(dados.get('persona', '')).strip()
        nivel = str(dados.get('nivel') or 'completo')
        if not texto or not persona:
            return self._json(400, {'erro': 'texto ou persona vazios'})
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

        def enviar(obj):
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + '\n').encode('utf-8'))
            self.wfile.flush()

        inicio = time.time()
        try:
            self.cerebro.turno(texto, persona, nivel, lambda t: enviar({'t': 'd', 'x': t}), enviar)
            enviar({'t': 'fim'})
            log('respondeu em %.1f s' % (time.time() - inicio))
        except ErroCerebro as e:
            log('erro no cérebro: %s' % e)
            try:
                enviar({'t': 'erro', 'msg': str(e), 'tipo': e.tipo})
            except OSError:
                pass
        except OSError:
            pass                                               # a página fechou a conexão (interrompida)

    def log_message(self, fmt, *args):
        pass


class Servidor(ThreadingHTTPServer):
    # No Windows, "reusar o endereço" deixa um segundo servidor ocupar a mesma porta sem erro nenhum:
    # o Jarvis antigo continuaria atendendo a página depois de uma atualização. Lá a porta fica exclusiva.
    allow_reuse_address = os.name != 'nt'


def achar_edge():
    """O Edge vem em todo Windows 10 e 11; procura nos lugares de instalação de sempre."""
    if os.name != 'nt':
        return None
    for base in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if base:
            exe = Path(base) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
            if exe.exists():
                return exe
    return None


def abrir_pagina(url, navegador='edge'):
    # No Edge o Jarvis tem as vozes naturais da Microsoft (Antonio), geradas na nuvem, sem gastar o processador.
    # Abre como aplicativo: janela própria, sem abas nem barra de endereço.
    edge = achar_edge() if navegador == 'edge' else None
    if edge:
        try:
            subprocess.Popen([str(edge), '--app=' + url, '--no-first-run', '--no-default-browser-check'], close_fds=True)
            log('abrindo o Jarvis numa janela do Edge (voz natural da Microsoft)')
            return
        except OSError as e:
            log('não consegui abrir o Edge (%s); usando o navegador padrão' % e)
    webbrowser.open(url)


def porta_ouvindo(porta):
    try:
        with socket.create_connection(('127.0.0.1', porta), timeout=1):
            return True
    except OSError:
        return False


def abrir_servidor(porta):
    try:
        return Servidor(('127.0.0.1', porta), Handler)
    except OSError:
        pass
    if porta_ouvindo(porta):
        log('já existe um Jarvis aberto: trocando pela versão desta pasta...')
        try:
            pedido = urllib.request.Request('http://127.0.0.1:%d/desligar' % porta, data=b'{}', method='POST')
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(pedido, timeout=3)
        except Exception:
            pass
    for _ in range(20):                                        # espera o antigo soltar a porta (até uns 10 s)
        time.sleep(0.5)
        try:
            return Servidor(('127.0.0.1', porta), Handler)
        except OSError:
            if not porta_ouvindo(porta):                       # ninguém mais ouvindo: são só conexões antigas terminando
                try:
                    return ThreadingHTTPServer(('127.0.0.1', porta), Handler)
                except OSError:
                    pass
    sys.exit('A porta %d está ocupada. Feche a outra janela do Jarvis e abra de novo.' % porta)


def main():
    ap = argparse.ArgumentParser(description='Servidor local do Jarvis 2')
    ap.add_argument('--porta', type=int, default=8880)
    ap.add_argument('--cofre', default='', help='pasta do seu cofre do Obsidian (as notas vão para <cofre>/Jarvis)')
    ap.add_argument('--modelo-rapido', default='haiku', help='modelo para pedidos simples (padrão: haiku)')
    ap.add_argument('--modelo-completo', default='sonnet', help='modelo para pedidos complexos (padrão: sonnet)')
    ap.add_argument('--modelo-maximo', default='opus', help='modelo para raciocínio profundo (padrão: opus; "nenhum" desliga)')
    ap.add_argument('--modelo', default='', help=argparse.SUPPRESS)
    ap.add_argument('--esforco', default='medium', help='esforço de raciocínio do modelo completo: low, medium ou high')
    ap.add_argument('--esforco-maximo', default='high', help='esforço de raciocínio do modelo máximo: low, medium ou high')
    ap.add_argument('--repouso', type=int, default=20, help='minutos sem uso até o cérebro descansar e liberar memória (0 = nunca)')
    ap.add_argument('--voz-modelo', default='fp16', choices=sorted(ONNX_MODELOS), help='modelo da voz ONNX: fp16 (padrão) ou fp32 (mesma velocidade, arquivo maior)')
    ap.add_argument('--sem-voz', action='store_true', help='não carrega o Kokoro')
    ap.add_argument('--teste', action='store_true', help='voz sintética, para testar a ligação')
    ap.add_argument('--claude', default='', help='caminho do executável do Claude Code (opcional)')
    ap.add_argument('--abrir', action='store_true', help='abre o jarvis.html quando estiver pronto')
    ap.add_argument('--navegador', default='edge', choices=('edge', 'padrao'),
                    help='edge (padrão: janela própria do Edge, com a voz natural da Microsoft) ou padrao (o navegador padrão do Windows)')
    a = ap.parse_args()

    CASA.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':                                        # prioridade um pouco acima do normal: a voz não espera o navegador
        try:
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00008000)
        except Exception:
            pass
    cofre = Path(a.cofre).expanduser() if a.cofre else None
    if cofre and not cofre.is_dir():
        sys.exit('Cofre não encontrado: %s' % cofre)
    Handler.notas = Notas((cofre / 'Jarvis') if cofre else (CASA / 'memoria'))
    log('notas em Markdown: %s' % Handler.notas.pasta)

    if a.sem_voz:
        Handler.voz = None
    elif a.teste:
        Handler.voz = Voz('teste')
    else:
        try:
            log('carregando a voz de mago (Kokoro, no processador)...')
            Handler.voz = Voz('kokoro', a.voz_modelo)
            if Handler.voz.pronta():
                Handler.voz.falar('Pronto.', VOZ_PADRAO, 1.0)
                log('voz pronta: %s' % VOZ_PADRAO)
        except ImportError:
            log('Kokoro não instalado (pip install kokoro-onnx): o Jarvis vai usar a voz do navegador')
            Handler.voz = None
        except Exception as e:
            log('a voz Kokoro falhou (%s): o Jarvis vai usar a voz do navegador' % e)
            Handler.voz = None

    comando = Cerebro.localizar(a.claude or None)
    if comando:
        modelos = {'rapido': a.modelo_rapido, 'completo': a.modelo or a.modelo_completo}
        if a.modelo_maximo.strip().lower() not in ('', 'nenhum', 'none', 'off', 'nao', 'não'):
            modelos['maximo'] = a.modelo_maximo
        Handler.cerebro = Hibrido(comando, modelos, gerar_mcp(cofre))
        log('cérebro híbrido na sua assinatura: %s' % ', '.join('%s = %s' % (k.replace('rapido', 'rápido').replace('maximo', 'máximo'), v)
                                                              for k, v in modelos.items()))
        log('skills instaladas encontradas: %d' % len(listar_skills()))
    else:
        log('Claude Code não encontrado: instale e faça login (rode "claude" uma vez). Sem ele, o Jarvis usa a chave da API.')

    srv = abrir_servidor(a.porta)                              # se já tem um Jarvis aberto, pede para ele fechar e assume
    log('Jarvis pronto em http://127.0.0.1:%d  (Ctrl+C para desligar)' % a.porta)
    if Handler.cerebro:
        def preparar():                                        # só depois de ocupar a porta, para não deixar processos órfãos
            if suporta_esforco(comando):                       # Claude Code antigo não conhece --effort
                for nivel, esforco in (('completo', a.esforco), ('maximo', a.esforco_maximo)):
                    if esforco and nivel in Handler.cerebro.cerebros:
                        Handler.cerebro.cerebros[nivel].esforco = esforco
                        log('esforço de raciocínio do modelo %s: %s' % (nivel.replace('maximo', 'máximo'), esforco))
            Handler.cerebro.aquecer()                          # deixa o rápido e o completo de pé
            while a.repouso:                                   # e, sem uso por um tempo, deixa o computador mais leve
                time.sleep(60)
                Handler.cerebro.repousar_se_ocioso(a.repouso)
        threading.Thread(target=preparar, daemon=True).start()
    if a.abrir:
        if (Path(__file__).resolve().parent / 'jarvis.html').exists():
            abrir_pagina('http://127.0.0.1:%d/' % a.porta, a.navegador)   # pelo servidor: microfone liberado e cérebro conectado
        else:
            log('jarvis.html não encontrado ao lado do servidor: copie-o para %s' % Path(__file__).resolve().parent)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if Handler.cerebro:
            Handler.cerebro.reiniciar()
        log('Jarvis desligado.')


if __name__ == '__main__':
    main()
