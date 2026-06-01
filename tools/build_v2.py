#!/usr/bin/env python3
"""Build v2 (combined) HTML bundle into server/v2/ with CDN-rewritten asset paths.

Reads: pipeline/gameflow/spec/r2_config.yaml (public URL)
Source: /Users/iuriinovosolov/Documents/image_prompts_experiment/combined_output/
        ep_NNN/ep_NNN.html  — HTML with quoted relative paths "audio/..." and "images/..."
        index.html          — episode list (self-contained)
Output: server/v2/ep_NNN/ep_NNN.html, server/v2/index.html

Rewrite rule — per episode NNN:
  "audio/FILE"   →  "<public_url>/ep_NNN/audio/FILE"
  "images/FILE"  →  "<public_url>/ep_NNN/images/FILE"

Usage:
  python3 tools/build_v2.py                     # все эпизоды, найденные в source
  python3 tools/build_v2.py 1 2 3               # конкретные
  python3 tools/build_v2.py --src /other/path   # alternate source
"""
import argparse
import hashlib
import html as html_mod
import json
import pathlib
import random
import re
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SRC = pathlib.Path(
    "/Users/iuriinovosolov/Documents/image_prompts_experiment/combined_output"
)
LANG = "uk"  # combined_output — украинский; если появится RU-сборка, будет "ru"
OUT_DIR = REPO / "server" / "v2" / LANG
CONFIG = REPO / "pipeline" / "gameflow" / "spec" / "r2_config.yaml"


def load_public_url() -> str:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    url = cfg["public_url"].rstrip("/")
    return url


BODY_INJECTION = (
    '<div id="v2-audio-controls">'
    '<button id="v2-rate" type="button" title="Скорость 1× / 2×" aria-label="Скорость">1×</button>'
    '<button id="v2-play" type="button" title="Play / Pause" aria-label="Play/Pause">▶</button>'
    # Speaker toggle: переключает audio-mode ↔ text-mode (проксирует #text-toggle).
    # 🔊 = аудио включено, 🔇 = текстовая мода.
    '<button id="v2-speaker" type="button" title="Динамик: аудио / текст" aria-label="Динамик">🔇</button>'
    '<button id="v2-lang" type="button" title="Перейти до RU-версії" aria-label="RU">🌐 RU</button>'
    '</div>\n'
    '<div id="v2-scene-badge">—</div>\n'
)

PROD_OVERRIDES = (
    '<style id="v2-prod-overrides">'
    # Debug-панель из image_prompts_experiment перекрывает #btnNext на мобилках.
    '.ipe-debug{display:none!important}'
    # Источник рендерит верхний ряд .audio-controls (text-toggle + audio-pause)
    # и левую .episode-nav. Оба блока выносим наружу — сверху живёт только
    # v2-группа (rate / play / lang) и scene-badge.
    '.audio-controls,.episode-nav{display:none!important}'
    # v2 audio-controls: три кнопки в ряд, верх-право. Всегда видимы.
    '#v2-audio-controls{'
    'position:fixed;top:10px;right:12px;z-index:220;'
    'display:flex;gap:6px;align-items:center'
    '}'
    '#v2-audio-controls button{'
    'background:rgba(0,0,0,0.65);color:#fff;'
    'border:1px solid rgba(255,255,255,0.25);'
    'border-radius:8px;padding:7px 10px;line-height:1;cursor:pointer;'
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
    'font-size:13px;font-weight:600;min-width:36px;text-align:center'
    '}'
    '#v2-audio-controls button:hover{background:rgba(0,0,0,0.85)}'
    # Scene-badge: верх-лево, показывает "Сцена X / Y".
    '#v2-scene-badge::before{content:"Сцена ";font-weight:400;opacity:0.7}'
    '#v2-scene-badge{'
    'position:fixed;top:10px;left:12px;z-index:220;'
    'background:rgba(0,0,0,0.7);color:#fff;'
    'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
    'font-size:12px;font-weight:600;letter-spacing:0.02em;'
    'padding:6px 9px;border-radius:6px;'
    'border:1px solid rgba(255,255,255,0.22);pointer-events:none'
    '}'
    # iOS Safari: `animation: fadeIn` с transform:translateY() на .scene
    # оставляет element в качестве containing block для position:fixed
    # даже после анимации → .story-choice (fixed;bottom:80px) попадает за
    # overflow:hidden. Убираем анимацию.
    '.scene{animation:none!important}'
    # Audio-mode: choice-кнопки появляются после того как аудио-очередь
    # для активной сцены опустела (class `.audio-done`, ставит JS ниже).
    'body.audio-mode section.scene:not(.audio-done) .story-choice{'
    'display:none!important'
    '}'
    # Text-mode: апстрим ставит .story-choice {position:fixed; bottom:80px}
    # — overlay перекрывает текст сцены, читать невозможно. В text-mode
    # вытаскиваем choice в обычный поток ПОД текстом (static), text не
    # прячем. Audio-mode не трогаем — там голос уже отыграл, оверлей ок.
    # Поведение: пока юзер не нажал «Дальше» — choice скрыт; после клика
    # ставим .v2-choice-revealed → choice появляется блоком после текста.
    'body:not(.audio-mode) section.scene.active:not(.v2-choice-revealed) .story-choice{'
    'display:none!important'
    '}'
    'body:not(.audio-mode) section.scene.active.v2-choice-revealed .story-choice{'
    'position:static!important;bottom:auto!important;left:auto!important;'
    'right:auto!important;padding:0!important;margin-top:1.25rem!important;'
    'z-index:auto!important'
    '}'
    # --- Фикс невидимых choice-кнопок на iOS Safari ---
    # Гипотеза: .story-choice (position:fixed) внутри активной .scene,
    # у которой overflow:hidden, на iOS хит-тестится по viewport, но
    # ПАЙНТ клиппится ancestor layer-ом. Даёт ровно симптом: DOM есть,
    # tap работает, на экране ничего. Лечим overflow на активной сцене.
    'body.audio-mode section.scene.active{overflow:visible!important}'
    # Дополнительно: у .nav-bar базовое правило ставит backdrop-filter:
    # blur(12px). На iOS это — классический триггер странного
    # compositing-поведения соседних fixed-слоёв. Сбрасываем.
    '.nav-bar{'
    '-webkit-backdrop-filter:none!important;'
    'backdrop-filter:none!important'
    '}'
    '</style>\n'
    '<script id="v2-audio-done-tracker">\n'
    '(function () {\n'
    '  var playingSet = new Set();\n'
    '  // Timestamp момента, когда в последний раз ЧТО-ТО играло.\n'
    '  // Если сейчас ничего не играет и с того момента прошло >SILENCE_MS —\n'
    '  // считаем, что автор дочитал, и показываем кнопки выбора.\n'
    '  var lastActivityAt = Date.now();\n'
    '  var SILENCE_MS = 300;\n'
    '  var currentRate = 1;  // playback rate (1x / 2x)\n'
    '\n'
    '  // Patch HTMLAudioElement.prototype.play — на первый play() каждому\n'
    '  // аудио навешиваем трекинг. Работает для всех способов создания.\n'
    '  var origPlay = HTMLAudioElement.prototype.play;\n'
    '  HTMLAudioElement.prototype.play = function () {\n'
    '    var a = this;\n'
    '    if (!a.__v2Tracked) {\n'
    '      a.__v2Tracked = true;\n'
    '      a.addEventListener("play",  function () { playingSet.add(a); lastActivityAt = Date.now(); });\n'
    '      a.addEventListener("pause", function () { playingSet.delete(a); });\n'
    '      a.addEventListener("ended", function () { playingSet.delete(a); });\n'
    '      a.addEventListener("error", function () { playingSet.delete(a); });\n'
    '    }\n'
    '    lastActivityAt = Date.now();\n'
    '    try { a.playbackRate = currentRate; } catch (_) {}\n'
    '    return origPlay.apply(a, arguments);\n'
    '  };\n'
    '\n'
    '  function anyPlaying() {\n'
    '    var r = false;\n'
    '    playingSet.forEach(function (a) { if (!a.paused && !a.ended) r = true; });\n'
    '    return r;\n'
    '  }\n'
    '\n'
    '  function audioDoneCheck() {\n'
    '    var active = document.querySelector(".scene.active");\n'
    '    if (!active) return;\n'
    '    if (!document.body.classList.contains("audio-mode")) {\n'
    '      active.classList.add("audio-done");\n'
    '      return;\n'
    '    }\n'
    '    if (anyPlaying()) {\n'
    '      lastActivityAt = Date.now();\n'
    '      active.classList.remove("audio-done");\n'
    '      return;\n'
    '    }\n'
    '    if (Date.now() - lastActivityAt > SILENCE_MS) {\n'
    '      active.classList.add("audio-done");\n'
    '    }\n'
    '  }\n'
    '\n'
    '  function updateBadge(sid) {\n'
    '    var badge = document.getElementById("v2-scene-badge");\n'
    '    if (!badge) return;\n'
    '    var scenes = document.querySelectorAll("section.scene");\n'
    '    var cur = sid ? document.querySelector(\'section.scene[data-scene-id="\' + sid + \'"]\') : null;\n'
    '    if (!cur || !scenes.length) { badge.textContent = ""; return; }\n'
    '    badge.textContent = (Array.from(scenes).indexOf(cur) + 1) + " / " + scenes.length;\n'
    '  }\n'
    '\n'
    '  // Text-mode choice-gate: пока юзер не нажал «Дальше» на сцене\n'
    '  // с .story-choice, апстрим-логика делает btnNext.disabled=true.\n'
    '  // Форс-разблокируем, чтобы юзер мог раскрыть choice нажатием.\n'
    '  function handleTextModeChoiceGate(active) {\n'
    '    if (document.body.classList.contains("audio-mode")) return;\n'
    '    if (!active.querySelector(".story-choice")) return;\n'
    '    if (active.classList.contains("v2-choice-revealed")) return;\n'
    '    if (active.querySelector(".choice-option.chosen")) return;\n'
    '    var btn = document.getElementById("btnNext");\n'
    '    if (btn && btn.disabled) btn.disabled = false;\n'
    '  }\n'
    '\n'
    '  // Audio-mode Марко auto-send: source-логика processNext на реплике\n'
    '  // Марка ставит chat-input-bar в imode-text/imode-voice и ЖДЁТ клика\n'
    '  // ➤ send (или mic). В audio-mode юзер не видит клавиатуру и кнопку\n'
    '  // «Далі» — для него сцена замирает (баг ep001_s04, ep007_s03,\n'
    '  // ep010_s02 и любая чат-сцена с репликой Марка). Авто-кликаем send/mic\n'
    '  // когда озвучка предыдущей реплики Софы доиграла + SILENCE_MS тишины.\n'
    '  function handleAudioModeMarko(active) {\n'
    '    if (!document.body.classList.contains("audio-mode")) return;\n'
    '    var bar = active.querySelector(".chat-input-bar");\n'
    '    if (!bar) return;\n'
    '    var isText = bar.classList.contains("imode-text");\n'
    '    var isVoice = bar.classList.contains("imode-voice");\n'
    '    if (!isText && !isVoice) {\n'
    '      // bar ушёл из активного режима (imode-disabled между Марко-репликами\n'
    '      // или после авто-отправки) → сбрасываем флаг для следующей.\n'
    '      delete bar.dataset.v2AutoSent;\n'
    '      return;\n'
    '    }\n'
    '    if (anyPlaying()) return;\n'
    '    if (Date.now() - lastActivityAt < SILENCE_MS) return;\n'
    '    if (bar.dataset.v2AutoSent === "1") return;\n'
    '    bar.dataset.v2AutoSent = "1";\n'
    '    if (isText) {\n'
    '      var sendBtn = bar.querySelector(".send-btn");\n'
    '      if (sendBtn) sendBtn.click();\n'
    '    } else {\n'
    '      var micBtn = bar.querySelector(".mic-btn");\n'
    '      if (micBtn) micBtn.click();\n'
    '    }\n'
    '  }\n'
    '\n'
    '  // Audio-mode unlock auto-click: source addUnlock рендерит .unlock-btn\n'
    '  // и ЖДЁТ клика юзера, иначе processNext не идёт к следующему msg\n'
    '  // (например, к голосовому Софии после "🔓 Розблокувати запис"\n'
    '  // в ep001_s04b). В audio-mode сам кликаем после тишины SILENCE_MS.\n'
    '  function handleAudioModeUnlock(active) {\n'
    '    if (!document.body.classList.contains("audio-mode")) return;\n'
    '    var btn = active.querySelector(".unlock-btn:not(.unlocked)");\n'
    '    if (!btn) return;\n'
    '    if (btn.dataset.v2AutoClicked === "1") return;\n'
    '    if (anyPlaying()) return;\n'
    '    if (Date.now() - lastActivityAt < SILENCE_MS) return;\n'
    '    btn.dataset.v2AutoClicked = "1";\n'
    '    btn.click();\n'
    '  }\n'
    '\n'
    '  function updateV2PlayLabel() {\n'
    '    var btn = document.getElementById("v2-play");\n'
    '    if (!btn) return;\n'
    '    var inAudio = document.body.classList.contains("audio-mode");\n'
    '    var pauseBtn = document.getElementById("audio-pause");\n'
    '    var playing = inAudio && pauseBtn &&\n'
    '                  pauseBtn.classList.contains("visible") &&\n'
    '                  pauseBtn.textContent === "⏸";\n'
    '    btn.textContent = playing ? "⏸" : "▶";\n'
    '  }\n'
    '\n'
    '  function updateV2SpeakerLabel() {\n'
    '    var btn = document.getElementById("v2-speaker");\n'
    '    if (!btn) return;\n'
    '    btn.textContent = document.body.classList.contains("audio-mode") ? "🔊" : "🔇";\n'
    '  }\n'
    '\n'
    '  var lastActiveSid = null;\n'
    '  function checkScene() {\n'
    '    var active = document.querySelector(".scene.active");\n'
    '    if (!active) return;\n'
    '    var sid = active.dataset.sceneId || "";\n'
    '    if (sid !== lastActiveSid) {\n'
    '      lastActiveSid = sid;\n'
    '      lastActivityAt = Date.now();\n'
    '      document.querySelectorAll("section.scene.audio-done").forEach(function (s) {\n'
    '        if (s !== active) s.classList.remove("audio-done");\n'
    '      });\n'
    '      document.querySelectorAll("section.scene.v2-choice-revealed").forEach(function (s) {\n'
    '        if (s !== active) s.classList.remove("v2-choice-revealed");\n'
    '      });\n'
    '      updateBadge(sid);\n'
    '    }\n'
    '    audioDoneCheck();\n'
    '    handleTextModeChoiceGate(active);\n'
    '    handleAudioModeMarko(active);\n'
    '    handleAudioModeUnlock(active);\n'
    '    updateV2PlayLabel();\n'
    '    updateV2SpeakerLabel();\n'
    '  }\n'
    '\n'
    '  function init() {\n'
    '    setInterval(checkScene, 250);\n'
    '\n'
    '    // Rate toggle (1× ↔ 2×)\n'
    '    var rateBtn = document.getElementById("v2-rate");\n'
    '    if (rateBtn) {\n'
    '      rateBtn.addEventListener("click", function (e) {\n'
    '        e.stopPropagation();\n'
    '        currentRate = currentRate === 1 ? 2 : 1;\n'
    '        rateBtn.textContent = currentRate + "×";\n'
    '        playingSet.forEach(function (a) {\n'
    '          try { a.playbackRate = currentRate; } catch (_) {}\n'
    '        });\n'
    '      });\n'
    '    }\n'
    '\n'
    '    // Play / Pause. Если audio-mode выключен — клик включает его\n'
    '    // (source-кнопка #text-toggle делает всю работу: переключает\n'
    '    // body.audio-mode и стартует очередь). Если включён — прокси к\n'
    '    // #audio-pause (pause/resume текущего трека).\n'
    '    var playBtn = document.getElementById("v2-play");\n'
    '    if (playBtn) {\n'
    '      playBtn.addEventListener("click", function (e) {\n'
    '        e.stopPropagation();\n'
    '        var inAudio = document.body.classList.contains("audio-mode");\n'
    '        if (!inAudio) {\n'
    '          var toggle = document.getElementById("text-toggle");\n'
    '          if (toggle) toggle.click();\n'
    '        } else {\n'
    '          var pauseBtn = document.getElementById("audio-pause");\n'
    '          if (pauseBtn) pauseBtn.click();\n'
    '        }\n'
    '      });\n'
    '    }\n'
    '\n'
    '    // Speaker toggle: проксирует #text-toggle (audio-mode ↔ text-mode).\n'
    '    // Сам source-кнопку прячем через .audio-controls{display:none},\n'
    '    // поэтому юзеру нужна доступная замена в v2-панели.\n'
    '    //\n'
    '    // Тонкость: source-скрипт на ПЕРВОМ pointerdown ЛЮБОГО элемента\n'
    '    // авто-кликает по #text-toggle (unlockAudio), чтобы преодолеть\n'
    '    // autoplay-block. Если мой click-handler ВСЛЕД ЗА ЭТИМ ещё раз\n'
    '    // кликнет toggle — двойной toggle обнулится.\n'
    '    // Решение: сохраняем audio-mode в pointerdown (мой capture-handler\n'
    '    // регистрируется раньше source unlock, потому что мой скрипт в\n'
    '    // <head>). В click сравниваем — если unlock уже переключил,\n'
    '    // не дублируем; иначе кликаем сами.\n'
    '    var preClickAudioMode = null;\n'
    '    document.addEventListener("pointerdown", function () {\n'
    '      preClickAudioMode = document.body.classList.contains("audio-mode");\n'
    '    }, true);\n'
    '    var speakerBtn = document.getElementById("v2-speaker");\n'
    '    if (speakerBtn) {\n'
    '      speakerBtn.addEventListener("click", function (e) {\n'
    '        e.stopPropagation();\n'
    '        var nowAudioMode = document.body.classList.contains("audio-mode");\n'
    '        if (preClickAudioMode !== null && nowAudioMode !== preClickAudioMode) {\n'
    '          // Unlock уже сделал toggle за нас — выходим, иначе сбросим.\n'
    '          return;\n'
    '        }\n'
    '        var toggle = document.getElementById("text-toggle");\n'
    '        if (toggle) toggle.click();\n'
    '      });\n'
    '    }\n'
    '\n'
    '    // Язык: /uk/ ↔ /ru/ в том же пути.\n'
    '    var langBtn = document.getElementById("v2-lang");\n'
    '    if (langBtn) {\n'
    '      langBtn.addEventListener("click", function (e) {\n'
    '        e.stopPropagation();\n'
    '        var p = location.pathname;\n'
    '        var next = null;\n'
    '        if (p.indexOf("/uk/") !== -1) next = p.replace("/uk/", "/ru/");\n'
    '        else if (p.indexOf("/ru/") !== -1) next = p.replace("/ru/", "/uk/");\n'
    '        if (next && next !== p) {\n'
    '          location.href = next + location.search + location.hash;\n'
    '        }\n'
    '      });\n'
    '    }\n'
    '\n'
    '    // DEBUG-кнопка: форс-скип на следующую сцену в обход игровой логики.\n'
    '    // Раньше делали прямой classList.add("active") — но source\n'
    '    // initPhoneChat вызывается ТОЛЬКО изнутри showScene(), которое\n'
    '    // источник держит в IIFE и наружу не выставляет. Из-за этого при\n'
    '    // дебаг-скипе на чат-сцену чат не инициализировался — пузыри\n'
    '    // не появлялись, btnNext висел disabled.\n'
    '    //\n'
    '    // Теперь идём через source goForward: снимаем все блокировки на\n'
    '    // активной сцене (chatPending, blocking, choice), помечаем\n'
    '    // v2-choice-revealed (чтобы наш capture-handler не перехватил),\n'
    '    // и кликаем штатный #btnNext. goForward → showScene → initPhoneChat.\n'
    '    var dbgBtn = document.getElementById("v2-debug-next");\n'
    '    if (dbgBtn) {\n'
    '      dbgBtn.addEventListener("click", function (e) {\n'
    '        e.stopPropagation();\n'
    '        var active = document.querySelector("section.scene.active");\n'
    '        if (!active) return;\n'
    '        // 1) Снять chat-pending (если чат ещё анимируется)\n'
    '        delete active.dataset.chatPending;\n'
    '        // 2) Снять blocking-флаг (квиз внутри чата требовал ответа)\n'
    '        if (active.dataset.blocking === "true") active.dataset.blocking = "false";\n'
    '        // 3) Если есть .story-choice — отметить первый вариант chosen,\n'
    '        //    чтобы updateNextButton разблокировал btnNext.\n'
    '        var firstChoice = active.querySelector(".choice-option:not(.chosen)");\n'
    '        if (firstChoice) firstChoice.classList.add("chosen");\n'
    '        // 4) Помечаем v2-choice-revealed — наш capture-handler сразу выйдет.\n'
    '        active.classList.add("v2-choice-revealed");\n'
    '        // 5) Принудительно включаем btnNext и кликаем.\n'
    '        var btn = document.getElementById("btnNext");\n'
    '        if (!btn) return;\n'
    '        btn.disabled = false;\n'
    '        // Если активная сцена — последняя в эпизоде, штатный goForward\n'
    '        // сам переведёт на data-next-ep. Если nextEp недоступен —\n'
    '        // покажем алерт.\n'
    '        var sceneEls = document.querySelectorAll("section.scene");\n'
    '        var isLast = sceneEls[sceneEls.length - 1] === active;\n'
    '        if (isLast && !active.dataset.nextEp) { alert("end of episodes"); return; }\n'
    '        btn.click();\n'
    '      });\n'
    '    }\n'
    '\n'
    '    // Text-mode choice-gate: перехват клика на #btnNext ДО основного\n'
    '    // handler\'а. Регистрируем на document в capture-фазе —\n'
    '    // document-handler отрабатывает раньше target-handler\'ов, и\n'
    '    // stopImmediatePropagation блокирует основной (который\n'
    '    // вызывает goForward и уводит сцену).\n'
    '    document.addEventListener("click", function (e) {\n'
    '      var t = e.target;\n'
    '      if (!t || t.id !== "btnNext") return;\n'
    '      if (document.body.classList.contains("audio-mode")) return;\n'
    '      var active = document.querySelector("section.scene.active");\n'
    '      if (!active) return;\n'
    '      if (!active.querySelector(".story-choice")) return;\n'
    '      if (active.classList.contains("v2-choice-revealed")) return;\n'
    '      if (active.querySelector(".choice-option.chosen")) return;\n'
    '      e.stopImmediatePropagation();\n'
    '      e.preventDefault();\n'
    '      active.classList.add("v2-choice-revealed");\n'
    '    }, true);\n'
    '  }\n'
    '\n'
    '  if (document.readyState === "loading") {\n'
    '    document.addEventListener("DOMContentLoaded", init);\n'
    '  } else {\n'
    '    init();\n'
    '  }\n'
    '})();\n'
    '</script>\n'
)


# Source иногда содержит image-messages с битым src вида `epNNN/X.webp`
# (плоский путь, без `../chat_images/`). Эти файлы factory НЕ
# сгенерил — на проде дают 404 + пустой bubble. Удаляем такие image
# целиком из чата. Валидные форматы:
#   ../chat_images/X      — обычный chat-image
#   chat_images/X         — без префикса (manifest-style, переписывается)
#   http(s)://... или data:  — абсолютный URL
_BROKEN_SRC_RE = re.compile(r'^ep\d{3}/.*\.(webp|png|jpg|jpeg)$', re.IGNORECASE)


def _is_broken_image_src(src):
    if not isinstance(src, str):
        return False
    return bool(_BROKEN_SRC_RE.match(src))


def _drop_broken_images(messages):
    """Remove image-messages where src points to a never-generated file
    (`epNNN/X.webp` plain-path). Returns (filtered_messages, n_dropped)."""
    if not isinstance(messages, list):
        return messages, 0
    out = []
    n_dropped = 0
    for m in messages:
        if (isinstance(m, dict) and m.get("t") == "image"
                and _is_broken_image_src(m.get("src"))):
            n_dropped += 1
            continue
        out.append(m)
    return out, n_dropped


def _reorder_messages(messages):
    """Pair leading image-block with following quizzes so each image lands right
    before its quiz (like a question preview). Then merge each image with the
    immediately-following text of same speaker into an image-with-caption.

    Why both passes:
      - In ep_037_s06 (and similar), source chat is `[img×10, intro_text×2,
        quiz, ans_text, quiz, ans_text, …]`. UX: 10 pictograms in a row before
        any context is overwhelming. Want `[intros, img+quiz+ans, img+quiz+ans,
        …]`. Move-images-before-quizzes pass handles this.
      - For other scenes where author wrote `[img, caption_text, img,
        caption_text, …]` we want classic Telegram «фото с подписью». Merge
        pass handles that.

    Bails (returns original) if structure doesn't match — never breaks data.
    """
    if not isinstance(messages, list) or len(messages) == 0:
        return messages, 0, 0

    # Pass 1: pair leading images with quizzes
    leading_images = []
    j = 0
    while j < len(messages) and isinstance(messages[j], dict) and messages[j].get("t") == "image":
        leading_images.append(messages[j])
        j += 1
    rest = messages[j:]
    quiz_positions = [k for k, m in enumerate(rest)
                      if isinstance(m, dict) and m.get("t") == "quiz"]
    n_paired = 0
    if len(leading_images) >= 2 and len(leading_images) == len(quiz_positions):
        # Insert from end so earlier indices stay stable
        result = list(rest)
        for k in range(len(leading_images) - 1, -1, -1):
            result.insert(quiz_positions[k], leading_images[k])
        n_paired = len(leading_images)
        messages = result

    # Pass 2: merge image+immediately-following-text-of-same-speaker into caption
    out = []
    n_merged = 0
    i = 0
    while i < len(messages):
        m = messages[i]
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        if (isinstance(m, dict) and m.get("t") == "image"
                and isinstance(nxt, dict) and nxt.get("t") == "text"
                and nxt.get("s") == m.get("s")
                and not m.get("caption")):
            merged = dict(m)
            merged["caption"] = nxt.get("x", "")
            out.append(merged)
            i += 2
            n_merged += 1
        else:
            out.append(m)
            i += 1

    return out, n_paired, n_merged


def reorder_chat_messages(html: str) -> tuple[str, int, int]:
    """Walk every data-chat-messages JSON in HTML and reorder image/text pairs.
    Returns (html, total_images_paired, total_image_text_merged, total_dropped_broken)."""
    pattern = re.compile(r'data-chat-messages="([^"]*)"')
    parts = []
    cursor = 0
    total_paired = 0
    total_merged = 0
    total_dropped = 0
    for m in pattern.finditer(html):
        parts.append(html[cursor:m.start()])
        encoded = m.group(1)
        try:
            messages = json.loads(html_mod.unescape(encoded))
        except json.JSONDecodeError:
            parts.append(html[m.start():m.end()])
            cursor = m.end()
            continue
        # NB: _drop_broken_images доступна, но НЕ вызываем — broken refs
        # (epNNN/X.webp) держим как 404, чтобы не потерять авторскую логику
        # квизов «угадай эмоцию по фото» (ep_014_s10/s17) и пиктограммам
        # ветвлений (ep_033_s01b). Реестр на дорисовку — pipeline/qa/
        # needed_images.md. Когда картинки появятся в R2 — 404 уйдут сами,
        # без правки билда.
        n_dropped = 0
        new_msgs, n_paired, n_merged = _reorder_messages(messages)
        total_paired += n_paired
        total_merged += n_merged
        total_dropped += n_dropped
        new_encoded = html_mod.escape(json.dumps(new_msgs, ensure_ascii=False), quote=True)
        parts.append(f'data-chat-messages="{new_encoded}"')
        cursor = m.end()
    parts.append(html[cursor:])
    return "".join(parts), total_paired, total_merged, total_dropped


def shuffle_quiz_options(html: str, ep_id: str) -> tuple[str, int]:
    """Reorder Telegram-chat quiz options inside `data-chat-messages` attributes
    so that correct answers are uniformly distributed across positions over the
    whole episode. Same algorithm as build_game.balance_episode_quizzes().

    Two-phase:
      1. Walk the entire HTML, collect every quiz option list across all
         data-chat-messages attributes, in document order.
      2. Group by len(options); for each group build round-robin target
         positions, shuffle with seed = sha1(ep_id + n_opts). Move the correct
         option in each quiz to its target position. Keep other options in
         their original relative order.

    Result: for an episode with 12 binary quizzes, exactly 6 have correct
    on top and 6 on bottom — no streaks, but order between specific quizzes
    stays stable across rebuilds (deterministic per-(episode, n_opts) seed).

    Returns (rewritten_html, n_quizzes_balanced).
    """
    pattern = re.compile(r'data-chat-messages="([^"]*)"')

    blocks = []
    for m in pattern.finditer(html):
        encoded = m.group(1)
        decoded = html_mod.unescape(encoded)
        try:
            messages = json.loads(decoded)
        except json.JSONDecodeError:
            blocks.append((m.start(), m.end(), None))
            continue
        if not isinstance(messages, list):
            blocks.append((m.start(), m.end(), None))
            continue
        blocks.append((m.start(), m.end(), messages))

    quiz_refs = []
    for _, _, messages in blocks:
        if messages is None:
            continue
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("t") != "quiz":
                continue
            opts = msg.get("o")
            if not isinstance(opts, list) or len(opts) < 2:
                continue
            quiz_refs.append(opts)

    from collections import defaultdict
    by_n = defaultdict(list)
    for opts in quiz_refs:
        by_n[len(opts)].append(opts)

    n_quizzes = 0
    for n_opts, group in by_n.items():
        if n_opts < 2:
            continue
        k = len(group)
        targets = [j % n_opts for j in range(k)]
        seed = int(hashlib.sha1(f"{ep_id}::{n_opts}".encode("utf-8")).hexdigest()[:12], 16)
        random.Random(seed).shuffle(targets)
        for opts, target in zip(group, targets):
            correct_idx = next(
                (i for i, o in enumerate(opts) if isinstance(o, dict) and o.get("c")),
                None,
            )
            if correct_idx is None:
                continue
            correct_obj = opts.pop(correct_idx)
            opts.insert(target, correct_obj)
            n_quizzes += 1

    rewritten_parts = []
    cursor = 0
    for start, end, messages in blocks:
        rewritten_parts.append(html[cursor:start])
        if messages is None:
            rewritten_parts.append(html[start:end])
        else:
            new_decoded = json.dumps(messages, ensure_ascii=False)
            new_encoded = html_mod.escape(new_decoded, quote=True)
            rewritten_parts.append(f'data-chat-messages="{new_encoded}"')
        cursor = end
    rewritten_parts.append(html[cursor:])
    return "".join(rewritten_parts), n_quizzes


def rewrite_html(html: str, public_url: str, nnn: str) -> tuple[str, int, int, int]:
    """Return (rewritten_html, n_audio, n_images, n_chat_images) counts."""
    base = f"{public_url}/ep_{nnn}"

    # Match "audio/<filename>" or "images/<filename>" in double quotes.
    audio_pat = re.compile(r'"audio/([^"]+)"')
    image_pat = re.compile(r'"images/([^"]+)"')
    # chat_images в source'е лежат в `data-chat-messages`-атрибуте,
    # который HTML-encoded: внутри него JSON-кавычки записаны как &quot;,
    # а путь — `../chat_images/FILE` (относительный из-за JS-префикса
    # `images/`, см. ниже). Переписываем на абсолютный CDN URL и дальше
    # патчим JS, чтобы он не префиксил абсолютные URL.
    chat_img_pat = re.compile(r'&quot;\.\./chat_images/([^&]+)&quot;')
    # Альтернативный формат для chat_images: `&quot;epNNN/X.ext&quot;` без
    # префикса `../chat_images/`. Встречается в ep_014_s10/s17 (квизы
    # «угадай эмоцию») и ep_033_s01b (пиктограммы веток s02). Эти ассеты
    # никогда не были сгенерены factory; реестр на дорисовку —
    # pipeline/qa/needed_images.md. Когда файлы появятся в R2 под
    # `ep_NNN/chat_images/<X>` — этот regex переписывает src на CDN URL,
    # и 404 уходит без правки данных.
    flat_chat_img_pat = re.compile(r'&quot;ep0*(\d+)/([^&]+)&quot;')
    # Per-option fail-аудио для constructor-квизов: URL'ы вида
    # `&quot;audio/epNNN_sNN_quiz_fail_optM.wav&quot;` (или _qNN_fail_optM)
    # внутри data-chat-messages JSON. Аналогично chat_img_pat — пишем
    # абсолютный CDN URL, чтобы addQuiz при wrong-клике подцепил аудио
    # напрямую через `new Audio(b.dataset.a)`.
    chat_audio_pat = re.compile(r'&quot;audio/([^&]+)&quot;')

    n_audio = 0
    n_images = 0
    n_chat = 0

    def sub_audio(m: re.Match) -> str:
        nonlocal n_audio
        n_audio += 1
        return f'"{base}/audio/{m.group(1)}"'

    def sub_image(m: re.Match) -> str:
        nonlocal n_images
        n_images += 1
        return f'"{base}/images/{m.group(1)}"'

    def sub_chat_img(m: re.Match) -> str:
        nonlocal n_chat
        n_chat += 1
        return f'&quot;{base}/chat_images/{m.group(1)}&quot;'

    def sub_flat_chat_img(m: re.Match) -> str:
        nonlocal n_chat
        n_chat += 1
        # Нормализуем номер эпизода в трёхзначный (ep14 → ep_014).
        target_nnn = m.group(1).zfill(3)
        return (f'&quot;{public_url}/ep_{target_nnn}/chat_images/'
                f'{m.group(2)}&quot;')

    def sub_chat_audio(m: re.Match) -> str:
        nonlocal n_audio
        n_audio += 1
        return f'&quot;{base}/audio/{m.group(1)}&quot;'

    # ПЕРВЫМ: патч JS-рендера addImage. В source-коде строка:
    #   '<img src="images/'+m.src+'"
    # То есть внутри src="..." сидит JS-конкатенация. Если сперва прогнать
    # image_pat regex, он захватит `"images/'+m.src+'"` как одну пару и
    # впишет CDN — строка развалится. Поэтому патчим JS РАНЬШЕ image/chat.
    # После патча m.src (абсолютный CDN для chat_images) летит в src без
    # префикса, обычные имена файлов префиксятся `images/`.
    old_js = '\'<img src="images/\'+m.src+\'"'
    new_js = '\'<img src="\'+(/:\\/\\//.test(m.src)?m.src:"images/"+m.src)+\'"'
    html = html.replace(old_js, new_js)

    # Патч extractChatText: для image-bubble озвучивать caption, а не
    # склеенный «Описание фото: <prompt>» + caption. Без этого
    # tracksByText не находит трек по тексту → первая реплика Софы
    # в _sofa-сценах с chat_image (ep002_s03_sofa, ep004_s10_sofa,
    # ep006_s07_sofa, ep008_s03b_sofa, ep010_s03_sofa, ep011_s04_sofa
    # и все сцены с реальными подписями) играет без звука.
    old_extract = (
        "      const bubble = node.querySelector('.bubble');\n"
        "      if (!bubble) return '';\n"
        "      // в пузыре есть sender-name, текст (div без класса), msg-time, иногда voice-*\n"
        "      // возьмём все div-дети и отфильтруем служебные\n"
        "      const clone = bubble.cloneNode(true);\n"
        "      clone.querySelectorAll(\n"
        "        '.sender-name, .msg-time, .voice-msg, .voice-subtitle, .quiz-options'\n"
        "      ).forEach(e => e.remove());\n"
        "      return clone.textContent.trim();"
    )
    new_extract = (
        "      const bubble = node.querySelector('.bubble');\n"
        "      if (!bubble) return '';\n"
        "      // v2 patch: image-bubble — caption и есть озвучиваемая реплика;\n"
        "      // .chat-image-fallback («Описание фото: <prompt>») — UI-only.\n"
        "      const __v2cap = bubble.querySelector('.chat-image-caption');\n"
        "      if (__v2cap) return __v2cap.textContent.trim();\n"
        "      // v2 patch: voice-bubble (msg-row Софии/Софы/Марка с .voice-msg) —\n"
        "      // .voice-subtitle и есть озвучиваемая реплика. Без этого\n"
        "      // unlock-голос Софии в ep001_s11 (\"Братику, почни зі щоденника…\")\n"
        "      // и любой другой voice в чате не матчится с track.text.\n"
        "      const __v2sub = bubble.querySelector('.voice-subtitle');\n"
        "      if (__v2sub) return __v2sub.textContent.trim();\n"
        "      const clone = bubble.cloneNode(true);\n"
        "      clone.querySelectorAll(\n"
        "        '.sender-name, .msg-time, .voice-msg, .voice-subtitle, .quiz-options, .chat-image, .chat-image-fallback'\n"
        "      ).forEach(e => e.remove());\n"
        "      return clone.textContent.trim();"
    )
    n_extract = html.count(old_extract)
    if n_extract != 1:
        print(f"  WARN: extractChatText-pattern matched {n_extract} times (expected 1)")
    html = html.replace(old_extract, new_extract)

    html = audio_pat.sub(sub_audio, html)
    html = image_pat.sub(sub_image, html)
    html = chat_img_pat.sub(sub_chat_img, html)
    html = flat_chat_img_pat.sub(sub_flat_chat_img, html)
    html = chat_audio_pat.sub(sub_chat_audio, html)
    # Сократить `pause_after_ms` между репликами в 4 раза (150→38, 400→100,
    # 800→200). Источник ставит 150/400/800 мс — для живой аудио-драмы это
    # много, особенно при ×2 playbackRate в debug-режиме. Делим на 4.
    pause_pat = re.compile(r'"pause_after_ms"\s*:\s*(\d+)')
    html = pause_pat.sub(
        lambda m: f'"pause_after_ms": {round(int(m.group(1)) / 4)}',
        html,
    )
    # Ускорить чат-анимацию (typing dots + интервалы между сообщениями).
    # Источник: var CFG={typingBase:200,typingRand:100,interMsg:150,...}.
    # Полная анимация одного сообщения: typingBase+typingRand+interMsg ≈ 450ms.
    # Юзер хочет суммарно ≤500ms на всю сцену с парой сообщений → режем.
    cfg_pat = re.compile(
        r'var CFG=\{typingBase:\d+,typingRand:\d+,interMsg:\d+,'
        r'afterSend:\d+,initDelay:\d+,voMin:\d+,voMax:\d+,'
        r'voFactor:\d+,retryDelay:\d+,recSim:\d+\}'
    )
    cfg_replacement = (
        'var CFG={typingBase:80,typingRand:40,interMsg:60,'
        'afterSend:80,initDelay:30,voMin:80,voMax:200,'
        'voFactor:4,retryDelay:200,recSim:200}'
    )
    n_cfg = len(cfg_pat.findall(html))
    if n_cfg != 1:
        # Не падаем — но фиксируем в логе, чтобы не упустить regression
        # (например, если апстрим переименовал поля).
        print(f"  WARN: CFG-pattern matched {n_cfg} times (expected 1)")
    html = cfg_pat.sub(cfg_replacement, html)
    # Инжекция prod-overrides (CSS + audio-done tracker) в <head>
    # и debug-skip-кнопки сразу после <body>.
    html = html.replace("</head>", PROD_OVERRIDES + "</head>", 1)
    # Источник пишет либо `<body>` (ep_001..005), либо `<body class="lang-uk">`
    # (ep_006+). Инжектим сразу после закрывающего `>` первого <body ...>.
    html = re.sub(
        r"(<body\b[^>]*>)",
        lambda m: m.group(1) + "\n" + BODY_INJECTION,
        html,
        count=1,
    )
    return html, n_audio, n_images, n_chat


def _strip_dangling_next_ep(html: str, src: pathlib.Path) -> tuple[str, int]:
    """Remove `data-next-ep="…"` if the target episode source HTML doesn't
    exist (last episode of a partially-built day). Otherwise debug-«⏭ Дальше»
    HEADs 404 + audio system also tries to preload.

    Source variants seen: `ep_041.html`, `../ep_041/ep_041.html`. Extract the
    `ep_NNN` token and check combined_output/ep_NNN/ep_NNN.html.
    """
    pat = re.compile(r'\sdata-next-ep="([^"]*ep_(\d{3})[^"]*\.html)"')
    n_stripped = 0

    def sub(m):
        nonlocal n_stripped
        target_nnn = m.group(2)
        target_src = src / f"ep_{target_nnn}" / f"ep_{target_nnn}.html"
        if target_src.exists():
            return m.group(0)
        n_stripped += 1
        return ""

    return pat.sub(sub, html), n_stripped


def build_episode(src: pathlib.Path, nnn: str, public_url: str) -> dict:
    src_html = src / f"ep_{nnn}" / f"ep_{nnn}.html"
    if not src_html.exists():
        return {"nnn": nnn, "skipped": True, "reason": f"missing {src_html}"}

    html = src_html.read_text(encoding="utf-8")
    html, n_paired, n_merged, n_dropped = reorder_chat_messages(html)
    html, n_next_stripped = _strip_dangling_next_ep(html, src)
    html, n_shuffled = shuffle_quiz_options(html, ep_id=str(int(nnn)))
    rewritten, n_audio, n_images, n_chat = rewrite_html(html, public_url, nnn)

    out_html = OUT_DIR / f"ep_{nnn}" / f"ep_{nnn}.html"
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(rewritten, encoding="utf-8")

    return {
        "nnn": nnn,
        "skipped": False,
        "audio": n_audio,
        "images": n_images,
        "chat_images": n_chat,
        "shuffled": n_shuffled,
        "paired": n_paired,
        "merged": n_merged,
        "dropped": n_dropped,
        "next_stripped": n_next_stripped,
        "out": out_html,
    }


BACK_LINK = (
    '<a href="/" style="display:inline-block;margin:0 auto 1rem;'
    'max-width:720px;width:100%;color:#8a8a8a;font-size:0.85rem;'
    'text-decoration:none;font-family:ui-monospace,SFMono-Regular,Menlo,monospace">'
    '← Главное меню</a>\n'
)


def build_index(src: pathlib.Path) -> pathlib.Path:
    src_idx = src / "index.html"
    out_idx = OUT_DIR / "index.html"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = src_idx.read_text(encoding="utf-8")
    html = html.replace("<body>", "<body>\n" + BACK_LINK, 1)
    out_idx.write_text(html, encoding="utf-8")
    return out_idx


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "episodes",
        nargs="*",
        help="Episode numbers (e.g. 1 2 5). Default — все ep_NNN, найденные в source",
    )
    p.add_argument("--src", default=str(DEFAULT_SRC))
    return p.parse_args()


def main() -> int:
    args = parse_args()
    src = pathlib.Path(args.src).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: source not found: {src}", file=sys.stderr)
        return 2

    public_url = load_public_url()
    print(f"public_url = {public_url}")
    print(f"source     = {src}")
    print(f"target     = {OUT_DIR}")

    if args.episodes:
        nums = [int(x) for x in args.episodes]
    else:
        nums = sorted(
            int(p.name.split("_", 1)[1])
            for p in src.glob("ep_*")
            if p.is_dir() and (p / f"{p.name}.html").exists()
        )
    nnns = [f"{n:03d}" for n in nums]

    idx_out = build_index(src)
    print(f"index      → {idx_out.relative_to(REPO)}")

    total_audio = 0
    total_images = 0
    total_chat = 0
    total_shuffled = 0
    total_paired = 0
    total_merged = 0
    total_dropped = 0
    total_next_stripped = 0
    built = 0
    skipped = []
    for nnn in nnns:
        r = build_episode(src, nnn, public_url)
        if r["skipped"]:
            skipped.append((nnn, r["reason"]))
            print(f"ep_{nnn}     — SKIP ({r['reason']})")
            continue
        built += 1
        total_audio += r["audio"]
        total_images += r["images"]
        total_chat += r["chat_images"]
        total_dropped += r["dropped"]
        total_next_stripped += r["next_stripped"]
        total_shuffled += r["shuffled"]
        total_paired += r["paired"]
        total_merged += r["merged"]
        extras = []
        if r["dropped"]: extras.append(f"dropped_broken_img: {r['dropped']}")
        if r["next_stripped"]: extras.append(f"stripped_dangling_next_ep: {r['next_stripped']}")
        extras_str = ("  ⚠ " + ", ".join(extras)) if extras else ""
        print(f"ep_{nnn}     → {r['out'].relative_to(REPO)}  "
              f"(audio: {r['audio']}, images: {r['images']}, "
              f"chat_images: {r['chat_images']}, "
              f"quiz_shuffled: {r['shuffled']}, "
              f"img→quiz: {r['paired']}, img+text: {r['merged']}){extras_str}")

    print()
    print(f"built {built}/{len(nnns)} episodes")
    print(f"rewrites — audio: {total_audio}, images: {total_images}, "
          f"chat_images: {total_chat}, quiz_shuffled: {total_shuffled}, "
          f"images→quizzes: {total_paired}, image+caption: {total_merged}, "
          f"dropped_broken_img: {total_dropped}, stripped_dangling_next_ep: {total_next_stripped}")
    if skipped:
        print(f"skipped {len(skipped)}:")
        for nnn, reason in skipped:
            print(f"  ep_{nnn}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
