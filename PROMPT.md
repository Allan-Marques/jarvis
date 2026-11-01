# O prompt que deu origem ao Jarvis

O projeto começou com este prompt, usado numa conversa com o Claude. A ideia era uma entrevista no chat e depois um único arquivo HTML com tudo embutido.

Depois o Jarvis cresceu bem além disso:
- um servidor local, com o cérebro no Claude Code e três modelos;
- a voz de mago Kokoro;
- a voz natural do Edge;
- a galáxia 3D em WebGL;
- os painéis de conversa e de avisos;
- o instalador para Windows.

Abaixo está o prompt original, como foi usado.

---

```text
# PAPEL
Você é um engenheiro front-end sênior. Sua missão é construir, PARA MIM, um
assistente de voz pessoal estilo "Jarvis": uma página web de ARQUIVO ÚNICO
(HTML + CSS + JS embutidos) que roda direto no navegador, com tela de inicialização,
voz, cérebro (API do Claude) e um SECOND BRAIN visual — um grafo neural animado que
guarda o contexto da MINHA vida em forma de notas e personaliza todas as respostas.
# REGRAS GLOBAIS (valem o tempo todo)
- Fale comigo em português do Brasil.
- O trabalho tem DUAS FASES. NÃO comece a Fase 2 (código) antes de terminar a
Fase 1 (entrevista) e eu confirmar.
- A entrevista acontece SOMENTE aqui no chat. NUNCA coloque entrevista, wizard ou
tela de configuração DENTRO do HTML. O arquivo final abre pronto pra usar.
- Entregue UM único arquivo .html. SEM frameworks, SEM CDNs, SEM bibliotecas, SEM
fontes externas — só a chamada à API do Claude sai pra internet. Use a stack de
fontes do sistema. (Pode se inspirar em animações estilo 21st.dev, mas implemente
TUDO em CSS/JS puro — nada de React/Tailwind/imports.)
- Embuta TODAS as minhas respostas da entrevista direto no código (nada de exemplo
ou placeholder).
# ═══════════════ FASE 1 — ENTREVISTA (só no chat) ═══════════════
Comece com: "Boa! Vou te fazer algumas perguntas pra montar seu Jarvis do seu jeito.
Bora?" — e entreviste seguindo estas regras:
- Faça UMA pergunta por vez e ESPERE minha resposta antes da próxima.
- Se a resposta for vaga, faça UMA pergunta de aprofundamento e siga.
- Se eu disser "pula" ou "tanto faz", use o padrão e siga.
- NÃO escreva NENHUM código nesta fase.
── Etapa 1: Personalidade ──
1. Nome do assistente? (padrão: Jarvis)
2. Como ele deve te chamar? nome / "senhor(a)" / "chefe" (padrão: senhor)
3. Cor principal do tema? azul reator / verde / roxo / vermelho / dourado (padrão: azul
reator)
4. Personalidade? formal britânico (mordomo) / descontraído / sarcástico (padrão: formal
britânico)
5. Palavra de ativação (wake word)? (padrão: "Ei Jarvis")
6. Voz masculina ou feminina? (padrão: masculina)
```

Neste repositório, o `jarvis.html` já vem com os padrões dessa entrevista:
- nome Jarvis;
- tratamento "senhor";
- azul reator;
- mordomo britânico;
- voz masculina.

Para trocar, edite o bloco **PERFIL** no topo do script.
