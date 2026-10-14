// El tapete de las mesas del casino: lo comparten las escenas que tienen fieltro
// (ruleta, autobús, bacará, dados, moneda, pollo, póker y backgammon) con un
// <script src="../tapete.js">.
//
// La skin llega en meta.skin (bot/services/skins.py, juego "tapete"):
//   felt     los tres colores del degradado del fieltro (luz, medio, borde).
//   pattern  el dibujo que va encima ("" = fieltro liso): ver PATTERNS.
// Con la skin de serie (key "serie") cada escena pinta sus propios colores de
// siempre, así la mesa de serie sale idéntica a la de antes de las skins.
//
// Uso en una escena:
//   const felt = Tapete.felt(META.skin, ["#238253", "#135a37", "#06281a"]);
//   ...el degradado de siempre con felt[0], felt[1] y felt[2]...
//   Tapete.pattern(g, 0, 0, W, H, META.skin);
// Si el escudo o la estrella de una bandera taparían algo de la mesa, la escena
// dice dónde va: Tapete.pattern(g, 0, 0, W, H, META.skin, [x, y, escala]).
//
// Imagen propia (dibujo "imagen", el Tapete propio): la imagen que sube cada
// dueño llega en meta.skin.image como data URL. Cargar una imagen es asíncrono
// en Chromium y en Node, así que el setup de cada escena empieza con
//   await Tapete.ready(meta?.skin);
// y luego pinta como siempre. Sin imagen, ready no hace nada.
// Y un texto claro que va sobre el tapete se lee con Tapete.outline (solo
// contornea si la skin lleva dibujo: la mesa de serie no cambia).
//
// Todo va dentro de Tapete para no pisar las funciones de las escenas.
"use strict";
window.Tapete = (() => {
  const TAU = Math.PI * 2;

  function rng(seed) {
    return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  }
  const linear = (g, x0, y0, x1, y1, stops) => {
    const gr = g.createLinearGradient(x0, y0, x1, y1);
    stops.forEach(([o, c]) => gr.addColorStop(o, c));
    return gr;
  };
  const radial = (g, x0, y0, r0, x1, y1, r1, stops) => {
    const gr = g.createRadialGradient(x0, y0, r0, x1, y1, r1);
    stops.forEach(([o, c]) => gr.addColorStop(o, c));
    return gr;
  };
  function starPath(g, cx, cy, r, inner, points = 5, rot = -Math.PI / 2) {
    g.beginPath();
    for (let i = 0; i < points * 2; i++) {
      const rad = i % 2 ? r * inner : r, a = rot + i * Math.PI / points;
      g.lineTo(cx + Math.cos(a) * rad, cy + Math.sin(a) * rad);
    }
    g.closePath();
  }
  function trianglePath(g, cx, cy, r, rot) {
    g.beginPath();
    for (let i = 0; i < 3; i++) {
      const a = rot + i * TAU / 3;
      g.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
    }
    g.closePath();
  }
  // Un brillo de cuatro puntas (las chispas del oro).
  function sparkle(g, x, y, r, color) {
    g.save(); g.fillStyle = color;
    starPath(g, x, y, r, 0.22, 4, 0); g.fill();
    g.restore();
  }

  // Pelusa y fibras del fieltro y la luz del techo: lo que hace que una bandera
  // parezca tela de mesa y no un dibujo plano.
  function feltFinish(g, w, h, seed) {
    const r = rng(seed), n = Math.round(w * h / 60);
    for (let i = 0; i < n; i++) {
      g.fillStyle = r() < 0.5 ? "rgba(255,255,255,0.035)" : "rgba(0,0,0,0.055)";
      g.fillRect(r() * w, r() * h, 1, 1);
    }
    g.lineWidth = 0.4;
    for (let i = 0; i < n / 18; i++) {
      const x = r() * w, y = r() * h, a = r() * TAU, l = 2 + r() * 4;
      g.strokeStyle = r() < 0.5 ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.07)";
      g.beginPath(); g.moveTo(x, y); g.lineTo(x + Math.cos(a) * l, y + Math.sin(a) * l); g.stroke();
    }
    const d = Math.hypot(w, h);
    g.fillStyle = radial(g, w * 0.45, h * 0.42, d * 0.05, w * 0.5, h * 0.5, d * 0.62, [
      [0, "rgba(255,248,225,0.10)"], [0.45, "rgba(0,0,0,0)"], [1, "rgba(0,0,0,0.42)"],
    ]);
    g.fillRect(0, 0, w, h);
  }

  // -- Tapete de oro ---------------------------------------------------------------------------
  // Fieltro dorado con una celosía art déco en relieve, abanicos en las esquinas y chispas.

  function oro(g, w, h) {
    const u = Math.min(w, h), d = Math.hypot(w, h);
    g.fillStyle = radial(g, w * 0.42, h * 0.4, 0, w * 0.5, h * 0.5, d * 0.6, [
      [0, "#ffe9a3"], [0.3, "#e2b54e"], [0.65, "#b07d22"], [1, "#5c3b07"],
    ]);
    g.fillRect(0, 0, w, h);
    // Brillo satinado en diagonal.
    for (const [o, a] of [[0.18, 0.10], [0.42, 0.07], [0.7, 0.09]]) {
      const cx = w * o, band = u * 0.22;
      g.fillStyle = linear(g, cx - band, 0, cx + band, h * 0.6, [
        [0, "rgba(255,250,220,0)"], [0.5, `rgba(255,250,220,${a})`], [1, "rgba(255,250,220,0)"],
      ]);
      g.fillRect(0, 0, w, h);
    }
    // Celosía de rombos: una línea oscura y, un píxel más arriba, una clara (relieve).
    const s = u / 7.5;
    g.lineWidth = Math.max(0.7, u / 500);
    for (const [dy, color] of [[0.9, "rgba(70,40,0,0.22)"], [0, "rgba(255,240,190,0.22)"]]) {
      g.strokeStyle = color; g.beginPath();
      for (let k = -h; k < w + h; k += s) {
        g.moveTo(k, dy); g.lineTo(k + h, h + dy);
        g.moveTo(k + h, dy); g.lineTo(k, h + dy);
      }
      g.stroke();
    }
    // Un rombo pequeño en el centro de cada celda y chispas en algunos cruces.
    const r = rng(5);
    for (let j = 0; j * s / 2 < h + s; j++) {
      for (let i = 0; i * s < w + s; i++) {
        const x = i * s + (j % 2 ? s / 2 : 0), y = j * s / 2;
        g.save(); g.translate(x, y + s / 2 * 0);
        g.beginPath(); g.moveTo(0, -s * 0.09); g.lineTo(s * 0.06, 0); g.lineTo(0, s * 0.09); g.lineTo(-s * 0.06, 0); g.closePath();
        g.fillStyle = "rgba(255,236,170,0.20)"; g.fill();
        g.restore();
        if (r() < 0.22) sparkle(g, x + s / 2, y, s * (0.07 + r() * 0.07), `rgba(255,253,235,${0.35 + r() * 0.4})`);
      }
    }
    // Abanicos art déco en las cuatro esquinas.
    for (const [cx, cy, a0] of [[0, 0, 0], [w, 0, Math.PI / 2], [w, h, Math.PI], [0, h, Math.PI * 1.5]]) {
      g.save(); g.translate(cx, cy); g.rotate(a0);
      const R = u * 0.34;
      for (let k = 1; k <= 4; k++) {
        g.beginPath(); g.arc(0, 0, R * k / 4, 0, Math.PI / 2);
        g.strokeStyle = `rgba(255,238,180,${0.30 - k * 0.04})`; g.lineWidth = u / 260; g.stroke();
      }
      for (let k = 0; k <= 8; k++) {
        const a = k * Math.PI / 16;
        g.beginPath(); g.moveTo(Math.cos(a) * R * 0.25, Math.sin(a) * R * 0.25); g.lineTo(Math.cos(a) * R, Math.sin(a) * R);
        g.strokeStyle = "rgba(255,238,180,0.16)"; g.lineWidth = u / 420; g.stroke();
      }
      g.beginPath(); g.arc(0, 0, R * 0.25, 0, Math.PI / 2); g.lineTo(0, 0); g.closePath();
      g.fillStyle = "rgba(255,238,180,0.18)"; g.fill();
      g.restore();
    }
    // Doble filete dorado alrededor.
    g.strokeStyle = "rgba(255,240,190,0.5)"; g.lineWidth = u / 240;
    g.strokeRect(u * 0.025, u * 0.025, w - u * 0.05, h - u * 0.05);
    g.strokeStyle = "rgba(90,55,5,0.45)"; g.lineWidth = u / 400;
    g.strokeRect(u * 0.037, u * 0.037, w - u * 0.074, h - u * 0.074);
  }

  // -- Bandera de Israel -----------------------------------------------------------------------
  // Blanco satinado con las dos franjas azules y la estrella de David, y un damasco de
  // estrellas muy suave en el blanco para que siga pareciendo tela.

  const ISRAEL_BLUE = "#0038b8";
  function hexagram(g, cx, cy, R, width, stroke) {
    g.lineJoin = "miter"; g.lineWidth = width; g.strokeStyle = stroke;
    trianglePath(g, cx, cy, R, -Math.PI / 2); g.stroke();
    trianglePath(g, cx, cy, R, Math.PI / 2); g.stroke();
  }
  function israel(g, w, h, [ex, ey, es] = [w / 2, h / 2, 1]) {
    g.fillStyle = linear(g, 0, 0, w * 0.3, h, [[0, "#ffffff"], [0.5, "#f1f4f9"], [1, "#dfe5ee"]]);
    g.fillRect(0, 0, w, h);
    // Damasco: estrellas pequeñas al tresbolillo.
    const s = Math.min(w, h) / 6.5;
    for (let j = 0; j * s * 0.86 < h + s; j++) {
      for (let i = -1; i * s < w + s; i++) {
        hexagram(g, i * s + (j % 2 ? s / 2 : 0), j * s * 0.86, s * 0.16, s * 0.022, "rgba(0,56,184,0.07)");
      }
    }
    // Las franjas (en la bandera, a 15/160 del borde y 25/160 de ancho).
    for (const [y0, y1] of [[0.094, 0.25], [0.75, 0.906]]) {
      const top = h * y0, bot = h * y1;
      g.fillStyle = linear(g, 0, top, 0, bot, [[0, "#1d52d0"], [0.5, ISRAEL_BLUE], [1, "#002c93"]]);
      g.fillRect(0, top, w, bot - top);
      g.fillStyle = "rgba(255,255,255,0.22)"; g.fillRect(0, top, w, Math.max(0.6, h * 0.004));
      g.fillStyle = "rgba(0,0,40,0.25)"; g.fillRect(0, bot - Math.max(0.6, h * 0.004), w, Math.max(0.6, h * 0.004));
    }
    // La estrella, con una sombra suave como si estuviera bordada.
    const R = h * 0.19 * es, lw = h * 0.034 * es;
    g.save(); g.shadowColor = "rgba(0,20,80,0.35)"; g.shadowBlur = h * 0.012; g.shadowOffsetY = h * 0.004;
    hexagram(g, ex, ey, R, lw, linear(g, 0, ey - R, 0, ey + R, [[0, "#1d52d0"], [1, "#002c93"]]));
    g.restore();
  }

  // -- Bandera de España -----------------------------------------------------------------------
  // Rojo y gualda (1:2:1) con el escudo a media altura del asta, como en la bandera.

  const ES_RED = "#aa151b", ES_GOLD = "#f1bf00";
  function espana(g, w, h, [ex, ey, es] = [Math.min(h / 2, w * 0.3), h / 2, 1]) {
    g.fillStyle = linear(g, 0, 0, 0, h, [[0, "#c21d24"], [0.25, ES_RED], [0.25, "#ffd21f"], [0.5, ES_GOLD], [0.75, "#e2b000"], [0.75, ES_RED], [1, "#7e0f13"]]);
    g.fillRect(0, 0, w, h);
    // Un hilo dorado entre las franjas, como en las banderas bordadas.
    for (const y of [h * 0.25, h * 0.75]) {
      g.fillStyle = "rgba(120,20,10,0.35)"; g.fillRect(0, y - h * 0.004, w, h * 0.008);
      g.fillStyle = "rgba(255,240,180,0.35)"; g.fillRect(0, y - h * 0.001, w, h * 0.002);
    }
    // Ondas del paño en el gualda.
    for (let k = 0; k < 5; k++) {
      const x = w * (0.1 + k * 0.22);
      g.fillStyle = linear(g, x - w * 0.08, 0, x + w * 0.08, 0, [[0, "rgba(255,255,255,0)"], [0.5, "rgba(255,255,255,0.08)"], [1, "rgba(0,0,0,0.05)"]]);
      g.fillRect(x - w * 0.08, 0, w * 0.16, h);
    }
    escudo(g, ex, ey, h * 0.4 * es / 100);
  }

  // El escudo en unidades de 100 de alto (de la cruz de la corona a la punta).
  function escudo(g, cx, cy, k) {
    g.save(); g.translate(cx, cy); g.scale(k, k);
    g.shadowColor = "rgba(60,20,0,0.35)"; g.shadowBlur = 3; g.shadowOffsetY = 1.2;
    for (const side of [-1, 1]) column(g, side * 37, side);
    g.shadowColor = "transparent";
    shield(g);
    crown(g, 0, -22, 1);
    g.restore();
  }
  function column(g, x, side) {
    const gold = linear(g, x - 6, 0, x + 6, 0, [[0, "#9a6a00"], [0.4, "#ffe680"], [1, "#a87800"]]);
    const silver = linear(g, x - 4, 0, x + 4, 0, [[0, "#8d939c"], [0.45, "#ffffff"], [1, "#9aa1aa"]]);
    // Las ondas del mar a los pies.
    for (let i = 0; i < 3; i++) {
      g.beginPath(); g.moveTo(x - 8, 41 + i * 2.6);
      for (let t = 0; t <= 4; t++) g.quadraticCurveTo(x - 7 + t * 4, 39.5 + i * 2.6 + (t % 2 ? 2 : -1), x - 6 + t * 4, 41 + i * 2.6);
      g.strokeStyle = i % 2 ? "#ffffff" : "#1f5fbf"; g.lineWidth = 1.4; g.stroke();
    }
    g.fillStyle = gold; g.fillRect(x - 7, 36, 14, 4); g.fillRect(x - 6, 33, 12, 3);
    g.fillStyle = silver; g.fillRect(x - 4.2, -14, 8.4, 47);
    g.strokeStyle = "rgba(80,80,90,0.5)"; g.lineWidth = 0.4;
    for (const dx of [-2, 0, 2]) { g.beginPath(); g.moveTo(x + dx, -13); g.lineTo(x + dx, 32); g.stroke(); }
    g.fillStyle = gold; g.fillRect(x - 6, -18, 12, 4); g.fillRect(x - 7, -20, 14, 2.4);
    crown(g, x, -21, 0.42);
    // La cinta roja con PLVS / VLTRA, enroscada a la columna.
    g.save(); g.translate(x, 10); g.rotate(side * 0.18);
    g.fillStyle = ES_RED; g.strokeStyle = "#5e0a0d"; g.lineWidth = 0.5;
    g.beginPath(); g.moveTo(-9, -3.6); g.quadraticCurveTo(0, -6, 9, -3.6); g.lineTo(9, 3.6); g.quadraticCurveTo(0, 1.2, -9, 3.6); g.closePath();
    g.fill(); g.stroke();
    g.fillStyle = "#ffd75a"; g.font = "700 4.2px Montserrat"; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillText(side < 0 ? "PLVS" : "VLTRA", 0, -0.6);
    g.restore();
  }
  // La corona real: aro con piedras, bonete rojo, diademas y el orbe con la cruz.
  function crown(g, x, y, s) {
    g.save(); g.translate(x, y); g.scale(s, s);
    const gold = linear(g, 0, -28, 0, 0, [[0, "#fff1a8"], [0.5, "#e8b923"], [1, "#9a6a00"]]);
    g.fillStyle = "#b5121b";
    g.beginPath(); g.moveTo(-17, -4); g.bezierCurveTo(-17, -24, 17, -24, 17, -4); g.closePath(); g.fill();
    g.strokeStyle = gold; g.lineWidth = 2.2;
    for (const dx of [-15, -7, 0, 7, 15]) {
      g.beginPath(); g.moveTo(dx, -4); g.quadraticCurveTo(dx * 0.55, -20, 0, -21); g.stroke();
    }
    for (const dx of [-15, -7, 0, 7, 15]) { g.beginPath(); g.arc(dx * 0.82, -8 - (15 - Math.abs(dx)) * 0.55, 1.4, 0, TAU); g.fillStyle = "#fff6c8"; g.fill(); }
    g.fillStyle = gold; g.fillRect(-19, -5, 38, 6);
    g.strokeStyle = "#7a5200"; g.lineWidth = 0.5; g.strokeRect(-19, -5, 38, 6);
    for (const [dx, c] of [[-13, "#1f8a3b"], [-4.5, "#c8102e"], [4.5, "#1f5fbf"], [13, "#1f8a3b"]]) {
      g.beginPath(); g.ellipse(dx, -2, 2.2, 1.6, 0, 0, TAU); g.fillStyle = c; g.fill();
    }
    g.beginPath(); g.arc(0, -24, 3, 0, TAU); g.fillStyle = gold; g.fill();
    g.fillRect(-0.8, -32, 1.6, 6); g.fillRect(-2.6, -30, 5.2, 1.6);
    g.restore();
  }
  function shield(g) {
    const L = -24, R = 24, T = -17, MID = 9, B = 44;
    const outline = () => {
      g.beginPath(); g.moveTo(L, T); g.lineTo(R, T); g.lineTo(R, 22);
      g.bezierCurveTo(R, 36, 12, B, 0, B); g.bezierCurveTo(-12, B, L, 36, L, 22); g.closePath();
    };
    g.save(); outline(); g.clip();
    // Castilla: castillo de oro sobre gules.
    g.fillStyle = "#c8102e"; g.fillRect(L, T, 24, MID - T);
    castle(g, -12, -4);
    // León: león púrpura sobre plata.
    g.fillStyle = "#f4f4f4"; g.fillRect(0, T, 24, MID - T);
    lion(g, 12, -4);
    // Aragón: cuatro palos de gules sobre oro.
    g.fillStyle = ES_GOLD; g.fillRect(L, MID, 24, B - MID);
    g.fillStyle = "#c8102e";
    for (let i = 0; i < 4; i++) g.fillRect(L + 2.7 + i * 5.4, MID, 2.7, B - MID);
    // Navarra: cadenas de oro sobre gules, con la esmeralda.
    g.fillStyle = "#c8102e"; g.fillRect(0, MID, 24, B - MID);
    g.strokeStyle = "#f6c700"; g.lineWidth = 1.3;
    g.strokeRect(2, MID + 2, 20, 26);
    g.beginPath(); g.moveTo(12, MID + 2); g.lineTo(12, MID + 28); g.moveTo(2, MID + 15); g.lineTo(22, MID + 15);
    g.moveTo(2, MID + 2); g.lineTo(22, MID + 28); g.moveTo(22, MID + 2); g.lineTo(2, MID + 28); g.stroke();
    g.beginPath(); g.arc(12, MID + 15, 2, 0, TAU); g.fillStyle = "#1f8a3b"; g.fill();
    // Granada, en la punta.
    g.beginPath(); g.moveTo(-9, B); g.quadraticCurveTo(-8, 34, 0, 31); g.quadraticCurveTo(8, 34, 9, B); g.closePath();
    g.fillStyle = "#f4f4f4"; g.fill();
    g.beginPath(); g.arc(0, 38.5, 3.2, 0, TAU); g.fillStyle = "#c8102e"; g.fill();
    g.fillStyle = "#1f8a3b"; g.beginPath(); g.ellipse(-2.6, 34.5, 2.2, 1, -0.6, 0, TAU); g.ellipse(2.6, 34.5, 2.2, 1, 0.6, 0, TAU); g.fill();
    g.restore();
    // Escusón de Borbón-Anjou: azur con tres flores de lis, bordura de gules.
    g.beginPath(); g.ellipse(0, MID, 7.6, 9, 0, 0, TAU); g.fillStyle = "#c8102e"; g.fill();
    g.beginPath(); g.ellipse(0, MID, 6, 7.4, 0, 0, TAU); g.fillStyle = "#1f3f9f"; g.fill();
    for (const [dx, dy] of [[-2.6, -2.6], [2.6, -2.6], [0, 2.6]]) fleur(g, dx, MID + dy);
    g.save(); outline(); g.lineWidth = 1.4; g.strokeStyle = "#7a5200"; g.stroke();
    g.lineWidth = 0.6; g.strokeStyle = "#ffe27a"; g.stroke(); g.restore();
  }
  function castle(g, x, y) {
    g.save(); g.translate(x, y); g.fillStyle = "#f6c700"; g.strokeStyle = "#7a5200"; g.lineWidth = 0.4;
    const block = (bx, by, bw, bh) => { g.fillRect(bx, by, bw, bh); g.strokeRect(bx, by, bw, bh); };
    block(-9, 0, 18, 9);
    block(-2.6, -9, 5.2, 9); block(-9, -5, 4.4, 5); block(4.6, -5, 4.4, 5);
    for (const [cx, cy] of [[-2.6, -11], [0.2, -11], [-9, -7], [-6.5, -7], [4.6, -7], [7.1, -7]]) block(cx, cy, 1.9, 2);
    g.fillStyle = "#1f3f9f";
    g.fillRect(-1.6, 3, 3.2, 6); g.fillRect(-0.9, -6.5, 1.8, 3);
    g.restore();
  }
  function lion(g, x, y) {
    g.save(); g.translate(x, y);
    g.fillStyle = "#6c2580"; g.strokeStyle = "#3a0f48"; g.lineWidth = 0.4;
    g.beginPath();
    g.moveTo(-3, 9); g.lineTo(-5, 3); g.quadraticCurveTo(-6, -2, -3, -4);   // pata y lomo
    g.lineTo(-1, -6); g.quadraticCurveTo(1, -10, 4, -9); g.lineTo(5.5, -7.5);  // cabeza
    g.lineTo(3.5, -6); g.lineTo(6, -3.5); g.lineTo(3.2, -3);                 // garra delantera alta
    g.lineTo(2, 0); g.lineTo(5, 1); g.lineTo(2.6, 2.2);                      // garra baja
    g.quadraticCurveTo(2, 6, 3, 9); g.lineTo(0.6, 9); g.lineTo(0, 5); g.lineTo(-1, 9); g.closePath();
    g.fill(); g.stroke();
    g.beginPath(); g.moveTo(-5, 4); g.bezierCurveTo(-10, 4, -6, -3, -9, -6); g.lineWidth = 1; g.strokeStyle = "#6c2580"; g.stroke();
    g.fillStyle = "#f6c700"; g.fillRect(0.8, -11.2, 3.6, 1.6);              // corona
    g.fillStyle = "#c8102e"; g.beginPath(); g.arc(5.2, -7.2, 0.6, 0, TAU); g.fill();   // lengua
    g.restore();
  }
  function fleur(g, x, y) {
    g.save(); g.translate(x, y); g.fillStyle = "#f6c700";
    g.beginPath(); g.ellipse(0, -0.6, 0.6, 1.5, 0, 0, TAU); g.fill();
    g.beginPath(); g.ellipse(-1, 0, 0.5, 1.1, -0.6, 0, TAU); g.ellipse(1, 0, 0.5, 1.1, 0.6, 0, TAU); g.fill();
    g.fillRect(-1.3, 0.6, 2.6, 0.5);
    g.restore();
  }

  // -- Canarias --------------------------------------------------------------------------------
  // Azul de océano con olas, el Teide nevado al fondo y la tricolor de las islas en el borde.

  function canarias(g, w, h) {
    const d = Math.hypot(w, h);
    g.fillStyle = radial(g, w * 0.4, h * 0.35, 0, w * 0.5, h * 0.5, d * 0.6, [
      [0, "#1b8fd6"], [0.45, "#0a62a8"], [1, "#03294f"],
    ]);
    g.fillRect(0, 0, w, h);
    // El Teide: el Pico Viejo a la izquierda, el cono y el pitón, con nieve en la cumbre.
    const peak = [w * 0.75, h * 0.47];
    const ridge = [
      [w * 0.30, h * 1.02], [w * 0.46, h * 0.86], [w * 0.56, h * 0.74], [w * 0.62, h * 0.66],
      [w * 0.655, h * 0.645], [w * 0.69, h * 0.6], [w * 0.725, h * 0.52], [w * 0.738, h * 0.488],
      peak, [w * 0.762, h * 0.488], [w * 0.778, h * 0.52], [w * 0.82, h * 0.6], [w * 0.88, h * 0.7],
      [w * 0.96, h * 0.8], [w * 1.05, h * 0.88], [w * 1.05, h * 1.02],
    ];
    const smooth = (pts) => {
      g.beginPath(); g.moveTo(...pts[0]);
      for (let i = 1; i < pts.length - 1; i++) {
        const mx = (pts[i][0] + pts[i + 1][0]) / 2, my = (pts[i][1] + pts[i + 1][1]) / 2;
        g.quadraticCurveTo(pts[i][0], pts[i][1], mx, my);
      }
      g.lineTo(...pts[pts.length - 1]); g.closePath();
    };
    smooth(ridge);
    g.fillStyle = linear(g, w * 0.6, h * 0.47, w * 0.9, h, [[0, "rgba(10,52,96,0.85)"], [1, "rgba(3,22,44,0.92)"]]);
    g.fill();
    // Luz del atardecer en la ladera izquierda.
    g.save(); smooth(ridge); g.clip();
    g.fillStyle = linear(g, w * 0.55, 0, w * 0.76, 0, [[0, "rgba(255,170,90,0)"], [1, "rgba(255,170,90,0.10)"]]);
    g.fillRect(w * 0.3, h * 0.45, w * 0.46, h * 0.6);
    // Nieve: la cumbre y lenguas que bajan por las cárcavas.
    g.fillStyle = "rgba(240,248,255,0.8)";
    g.beginPath(); g.moveTo(w * 0.7, h * 0.56);
    const r2 = rng(3);
    for (let k = 0; k <= 12; k++) {
      const x = w * (0.7 + k * 0.1 / 12), down = k % 2 ? 0.018 + r2() * 0.03 : 0.0;
      g.lineTo(x, h * (0.56 + down - Math.abs(k - 6) * 0.004));
    }
    g.lineTo(w * 0.82, h * 0.4); g.lineTo(w * 0.68, h * 0.4); g.closePath(); g.fill();
    g.restore();
    // El mar de nubes: muchas bolitas muy tenues a media ladera.
    const rc = rng(13);
    for (let i = 0; i < 260; i++) {
      const x = w * (0.32 + rc() * 0.74), y = h * (0.8 + (rc() - 0.5) * 0.08), rad = h * (0.012 + rc() * 0.028);
      g.beginPath(); g.arc(x, y, rad, 0, TAU); g.fillStyle = "rgba(225,240,255,0.045)"; g.fill();
    }
    // Olas.
    const r = rng(9);
    g.lineWidth = Math.max(0.6, h / 360); g.lineCap = "round";
    for (let i = 0; i < w * h / 900; i++) {
      const x = r() * w, y = r() * h, l = w * (0.012 + r() * 0.02);
      g.beginPath(); g.moveTo(x, y); g.quadraticCurveTo(x + l / 2, y - l * 0.35, x + l, y);
      g.strokeStyle = `rgba(200,235,255,${0.04 + r() * 0.06})`; g.stroke();
    }
    // La tricolor (blanco, azul y amarillo) a lo largo del borde izquierdo.
    const sw = Math.max(3, w * 0.012);
    [["#ffffff", 0], ["#0768a9", 1], ["#fcd116", 2]].forEach(([c, i]) => { g.fillStyle = c; g.fillRect(i * sw, 0, sw, h); });
    g.fillStyle = "rgba(0,0,0,0.3)"; g.fillRect(sw * 3, 0, Math.max(1, sw * 0.25), h);
  }

  // -- Imagen propia ---------------------------------------------------------------------------
  // La imagen del dueño a sangre, recortada para cubrir el tapete (como background-size: cover)
  // y un poco oscurecida para que se lean las fichas y los textos de la mesa encima.

  // Las últimas imágenes cargadas, por data URL. Pocas: cada dibujo usa una y en
  // una mesa compartida solo cambia al cambiar quien la abre.
  const IMAGES = new Map();
  const MAX_IMAGES = 4;

  function imagen(g, w, h, _emblem, skin) {
    const img = IMAGES.get(skin.image);
    const k = Math.max(w / img.width, h / img.height);
    const iw = img.width * k, ih = img.height * k;
    g.drawImage(img, (w - iw) / 2, (h - ih) / 2, iw, ih);
    g.fillStyle = "rgba(0,0,0,0.18)"; g.fillRect(0, 0, w, h);
  }

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("No se pudo leer la imagen del tapete"));
      img.src = src;
    });
  }

  // -- Puerto Rico -----------------------------------------------------------------------------
  // Las cinco franjas, el triángulo azul y la estrella blanca: la monoestrellada.

  function boricua(g, w, h) {
    for (let i = 0; i < 5; i++) {
      const y0 = h * i / 5, y1 = h * (i + 1) / 5;
      g.fillStyle = i % 2
        ? linear(g, 0, y0, 0, y1, [[0, "#ffffff"], [1, "#e8ebf0"]])
        : linear(g, 0, y0, 0, y1, [[0, "#f2163f"], [1, "#c90a33"]]);
      g.fillRect(0, y0, w, y1 - y0 + 0.5);
    }
    const tip = h * 0.866;
    g.save(); g.shadowColor = "rgba(0,0,0,0.35)"; g.shadowBlur = h * 0.02; g.shadowOffsetX = h * 0.006;
    g.beginPath(); g.moveTo(0, 0); g.lineTo(tip, h / 2); g.lineTo(0, h); g.closePath();
    g.fillStyle = linear(g, 0, 0, tip, h, [[0, "#1a64e8"], [1, "#0a3c9e"]]); g.fill();
    g.restore();
    g.save(); g.shadowColor = "rgba(0,0,40,0.4)"; g.shadowBlur = h * 0.01;
    starPath(g, tip / 3, h / 2, h * 0.14, 0.382); g.fillStyle = "#ffffff"; g.fill();
    g.restore();
  }

  const PATTERNS = { oro, israel, espana, canarias, boricua, imagen };
  // Si la skin trae un dibujo que se puede pintar: el de imagen, solo con su imagen ya cargada.
  const painter = (skin) => {
    const paint = skin && PATTERNS[skin.pattern];
    return paint === imagen && !IMAGES.has(skin.image) ? null : paint;
  };

  return {
    // Los colores del degradado del fieltro: los de la skin, o los de la escena si es la de serie.
    felt(skin, own) {
      return skin && skin.key !== "serie" && skin.felt ? skin.felt : own;
    },
    // El dibujo de la skin sobre el rectángulo del tapete (nada si no lleva).
    // `emblem`: [x, y, escala] del escudo o la estrella, en píxeles del rectángulo.
    pattern(g, x, y, w, h, skin, emblem) {
      const paint = painter(skin);
      if (!paint) return;
      g.save(); g.beginPath(); g.rect(x, y, w, h); g.clip(); g.translate(x, y);
      paint(g, w, h, emblem, skin);
      feltFinish(g, w, h, 21);
      g.restore();
    },
    // Contorno oscuro bajo un texto claro, para que se lea sobre una bandera.
    outline(g, skin, text, x, y, width = 3) {
      if (!painter(skin)) return;
      g.save(); g.lineJoin = "round"; g.lineWidth = width; g.strokeStyle = "rgba(0,0,0,0.7)";
      g.shadowColor = "transparent"; g.strokeText(text, x, y); g.restore();
    },
    // Carga la imagen propia de la skin, si trae, antes de pintar (ver arriba).
    async ready(skin) {
      const src = skin && skin.image;
      if (!src || IMAGES.has(src)) return;
      const img = await loadImage(src);
      if (IMAGES.size >= MAX_IMAGES) IMAGES.delete(IMAGES.keys().next().value);
      IMAGES.set(src, img);
    },
    PATTERNS: Object.keys(PATTERNS),
  };
})();
