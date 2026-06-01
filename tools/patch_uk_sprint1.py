#!/usr/bin/env python3
"""Sprint 1 UX patch: apply visual fixes to all server/v2/uk/ep_NNN HTML files.

Changes applied:
  - lang="ru" → lang="uk"
  - Russian nav/chat UI strings → Ukrainian
  - Remove v2-debug-next button from top controls
  - "🌐 Перевод" → "🌐 RU"
  - updateBadge: show "X / Y" scene counter instead of raw scene ID
  - Add #v2-scene-badge::before { content: "Сцена "; }
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
UK_DIR = REPO / "server" / "v2" / "uk"

STRING_REPLACEMENTS = [
    ('lang="ru"',              'lang="uk"'),
    ('>Дальше →<',             '>Далі →<'),
    ('Ожидайте...',            'Зачекайте...'),
    ('Голосовое',              'Голосовий'),
    ('Сообщение...',           'Повідомлення...'),
    ('Запись...',              'Запис...'),
    ('Конец эпизода',          'Кінець епізоду'),
    ('Правильных',             'Правильних'),
    # Unicode-escaped versions (same strings in JS)
    ('\\u041e\\u0436\\u0438\\u0434\\u0430\\u0439\\u0442\\u0435...',
     '\\u0417\\u0430\\u0447\\u0435\\u043a\\u0430\\u0439\\u0442\\u0435...'),
    ('\\u0413\\u043e\\u043b\\u043e\\u0441\\u043e\\u0432\\u043e\\u0435',
     '\\u0413\\u043e\\u043b\\u043e\\u0441\\u043e\\u0432\\u0438\\u0439'),
    ('\\u0417\\u0430\\u043f\\u0438\\u0441\\u044c...',
     '\\u0417\\u0430\\u043f\\u0438\\u0441......'),
    # "🌐 Перевод" → "🌐 RU"
    ('🌐 Перевод', '🌐 RU'),
    ('title="Перевод (UK ↔ RU)"', 'title="Перейти до RU-версії"'),
    ('aria-label="Перевод"', 'aria-label="RU"'),
]

# Regex to remove the entire v2-debug-next button element
DEBUG_BTN_RE = re.compile(
    r'<button\s+id="v2-debug-next"[^>]*>.*?</button>',
    re.DOTALL
)

OLD_UPDATE_BADGE = (
    '  function updateBadge(sid) {\n'
    '    var badge = document.getElementById("v2-scene-badge");\n'
    '    if (badge) badge.textContent = sid || "—";\n'
    '  }\n'
)

NEW_UPDATE_BADGE = (
    '  function updateBadge(sid) {\n'
    '    var badge = document.getElementById("v2-scene-badge");\n'
    '    if (!badge) return;\n'
    '    var scenes = document.querySelectorAll("section.scene");\n'
    '    var cur = sid ? document.querySelector(\'section.scene[data-scene-id="\' + sid + \'"]\') : null;\n'
    '    if (!cur || !scenes.length) { badge.textContent = ""; return; }\n'
    '    badge.textContent = (Array.from(scenes).indexOf(cur) + 1) + " / " + scenes.length;\n'
    '  }\n'
)

# CSS to add before closing </style> of PROD_OVERRIDES
BADGE_CSS_WRONG = '#v2-scene-badge::before{content:"\\u0421\\u0446\\u0435\\u043d\\u0430\\u00a0";font-weight:400;opacity:0.7}'
BADGE_CSS       = '#v2-scene-badge::before{content:"Сцена ";font-weight:400;opacity:0.7}'
PROD_STYLE_CLOSE = '</style>'


def patch_file(path: pathlib.Path) -> dict:
    text = path.read_text(encoding='utf-8')
    original = text
    changes = []

    # 1. String replacements
    for old, new in STRING_REPLACEMENTS:
        if old in text:
            count = text.count(old)
            text = text.replace(old, new)
            changes.append(f'  replaced {count}× "{old[:40]}"')

    # 2. Remove debug-next button
    new_text, n = DEBUG_BTN_RE.subn('', text)
    if n:
        text = new_text
        changes.append(f'  removed {n}× v2-debug-next button')

    # 3. Replace updateBadge
    if OLD_UPDATE_BADGE in text:
        text = text.replace(OLD_UPDATE_BADGE, NEW_UPDATE_BADGE, 1)
        changes.append('  replaced updateBadge()')
    elif '  function updateBadge(sid) {' in text:
        changes.append('  WARNING: updateBadge found but pattern did not match exactly')

    # 4. Replace wrong badge CSS → correct, or add if missing
    if BADGE_CSS_WRONG in text:
        text = text.replace(BADGE_CSS_WRONG, BADGE_CSS)
        changes.append('  fixed badge ::before CSS (encoding)')
    elif BADGE_CSS not in text and PROD_STYLE_CLOSE in text:
        text = text.replace(PROD_STYLE_CLOSE, BADGE_CSS + PROD_STYLE_CLOSE, 1)
        changes.append('  added badge ::before CSS')

    if text != original:
        path.write_text(text, encoding='utf-8')
        return {'changed': True, 'changes': changes}
    return {'changed': False, 'changes': []}


def main():
    ep_dirs = sorted(UK_DIR.glob('ep_*/ep_*.html'))
    if not ep_dirs:
        print(f'No episode files found in {UK_DIR}')
        sys.exit(1)

    changed = 0
    for path in ep_dirs:
        result = patch_file(path)
        ep = path.parent.name
        if result['changed']:
            changed += 1
            print(f'[OK] {ep}')
            for c in result['changes']:
                print(c)
        else:
            print(f'[--] {ep} (no changes)')

    print(f'\nDone: {changed}/{len(ep_dirs)} files patched.')


if __name__ == '__main__':
    main()
