// gigabite — pull all your claude.ai conversations from inside the browser.
// Runs on the claude.ai page, so it uses the browser's own session + Cloudflare
// clearance (the reason a terminal client gets a 403). Projectless AND project
// chats are included and tagged. Produces a `conversations.json` download that
// gigabite's importer already understands.
//
// HOW TO RUN (Safari):
//   1. Be logged in at https://claude.ai in the active tab.
//   2. Enable the Develop menu: Safari → Settings → Advanced →
//      "Show features for web developers".
//   3. Develop → Show JavaScript Console (or ⌥⌘C).
//   4. Paste this whole file, press Enter. If pasting is blocked, type
//      `allow pasting` first, Enter, then paste.
//   5. It downloads conversations.json to ~/Downloads. Then in a terminal:
//        mv ~/Downloads/conversations.json ~/Knowledge/.gigabite/imports/claude_ai/
//        gigabite ingest
//        gigabite materialize   # write each chat out as a file you can open
//
(async () => {
  const j = (u) => fetch(u, { headers: { accept: 'application/json' }, credentials: 'include' })
    .then(r => { if (!r.ok) throw new Error(u.split('?')[0] + ' -> ' + r.status); return r.json(); });

  const orgs = await j('/api/organizations');
  const org = (orgs.find(o => (o.capabilities || []).includes('chat')) || orgs[0]);
  const orgId = org.uuid;
  console.log('[gigabite] org:', orgId);

  const projMap = {};
  try {
    for (const p of (await j(`/api/organizations/${orgId}/projects`)) || []) projMap[p.uuid] = p.name;
    console.log('[gigabite] projects:', Object.keys(projMap).length);
  } catch (e) { console.warn('[gigabite] projects fetch failed (continuing):', e.message); }

  const list = await j(`/api/organizations/${orgId}/chat_conversations`);
  console.log('[gigabite] conversations listed:', list.length,
              '(if this looks capped, tell gigabite — pagination may be needed)');

  const out = [];
  for (let i = 0; i < list.length; i++) {
    const c = list[i];
    try {
      const full = await j(`/api/organizations/${orgId}/chat_conversations/${c.uuid}?tree=True&rendering_mode=raw`);
      full.project_name = projMap[c.project_uuid || full.project_uuid] || '';
      out.push(full);
    } catch (e) { console.warn('[gigabite] skipped', c.uuid, e.message); }
    if (i % 10 === 0) console.log(`[gigabite] ${i + 1}/${list.length}`);
    await new Promise(r => setTimeout(r, 120)); // be polite
  }

  const blob = new Blob([JSON.stringify(out)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'conversations.json';
  document.body.appendChild(a); a.click(); a.remove();
  console.log('[gigabite] DONE — downloaded conversations.json with', out.length, 'conversations');
})();
