// Pinta escenas de canvas (assets/*/escena.html) con Node y Skia, sin navegador.
//
// Las escenas se escribieron para Chromium, pero solo usan canvas 2D. Aquí se
// carga el <script> de cada escena.html con un `document` mínimo que crea
// lienzos de @napi-rs/canvas (Skia, el mismo motor que pinta el canvas en
// Chrome) y las fuentes de sus @font-face. Lo usa bot/services/node_scene.py.
//
// Un mismo proceso sirve a cualquier escena: cada una se carga la primera vez
// que se le pide algo, en su propio contexto (sus globales no pisan las de las
// demás), y se queda cargada. Si hay más de MAX_SCENES, se suelta la que lleva
// más tiempo sin usarse.
//
// Uso: node --expose-gc escena_node.cjs
//
// Protocolo por stdin/stdout, una petición por línea de JSON:
//   {"scene": ruta, "call": "setup", "args": [meta]}                -> {"ok": true}
//   {"scene": ruta, "call": "renderFrames", "args": [states, first]} -> {"ok": true, "frames": [[x, y, w, h], ...]}
//                                                       y detrás los píxeles RGBA de cada recuadro
//   (cualquier función que devuelva imágenes de exportFrame responde igual)
//   {"scene": ruta, "call": "<expr>", "eval": true}                  -> {"ok": true, "value": ...}
// Si algo falla: {"ok": false, "error": "..."}. Al arrancar escribe {"ready": true}.
//
// Los fotogramas salen en RGBA crudo y no en PNG: comprimir PNG en un solo hilo
// era lo más caro de dibujar en Chromium, y Python lo descomprimía otra vez.

"use strict";
const fs = require("fs");
const path = require("path");
const readline = require("readline");
const vm = require("vm");
const { createCanvas, GlobalFonts, Image, ImageData, Path2D, DOMMatrix } = require("@napi-rs/canvas");

// Escenas cargadas a la vez en un proceso; cada una guarda sus lienzos.
const MAX_SCENES = 3;
// Lo del entorno de Node que una escena de navegador puede usar.
const HOST = [
  "console", "setTimeout", "clearTimeout", "setInterval", "clearInterval", "queueMicrotask",
  "performance", "atob", "btoa", "TextEncoder", "TextDecoder", "structuredClone", "URL",
];

const document = {
  createElement(tag) {
    if (tag !== "canvas") throw new Error(`La escena pide un <${tag}>: sin navegador solo hay canvas`);
    return createCanvas(300, 150);
  },
  // Las fuentes ya están registradas antes de ejecutar la escena.
  fonts: { load: async () => [], ready: Promise.resolve(), check: () => true },
};
// La escena devuelve cada recuadro con exportFrame(lienzo); en Chromium es un
// data URL de PNG, aquí los píxeles tal cual.
const exportFrame = (canvas) => ({
  w: canvas.width,
  h: canvas.height,
  rgba: canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data,
});

const fonts = new Set();
const scenes = new Map();

function load(scenePath) {
  const html = fs.readFileSync(scenePath, "utf8");
  for (const [, family, url] of html.matchAll(/@font-face\s*{[^}]*font-family:\s*"([^"]+)"[^}]*url\("([^"]+)"\)/g)) {
    const file = path.resolve(path.dirname(scenePath), url);
    if (!fonts.has(file)) {
      GlobalFonts.registerFromPath(file, family);
      fonts.add(file);
    }
  }
  // `new Image()` para la imagen propia del tapete (tapete.js la carga de un data URL).
  const sandbox = { document, exportFrame, Image, ImageData, Path2D, DOMMatrix };
  for (const name of HOST) sandbox[name] = globalThis[name];
  const context = vm.createContext(sandbox);
  vm.runInContext("var window = globalThis, self = globalThis;", context);
  // Cada <script> por separado y en orden, como el navegador: los de `src` (el
  // tapete común, ../tapete.js) se leen del disco.
  for (const [, src, code] of html.matchAll(/<script(?:\s+src="([^"]+)")?>([\s\S]*?)<\/script>/g)) {
    const file = src ? path.resolve(path.dirname(scenePath), src) : scenePath;
    vm.runInContext(src ? fs.readFileSync(file, "utf8") : code, context, { filename: file });
  }
  // Para leer los argumentos con los objetos de la escena (ver `handle`).
  return { context, parse: vm.runInContext("JSON.parse", context) };
}

function scene(scenePath) {
  const key = path.resolve(scenePath);
  let loaded = scenes.get(key);
  if (loaded) {
    scenes.delete(key);
  } else {
    loaded = load(key);
    if (scenes.size >= MAX_SCENES) {
      scenes.delete(scenes.keys().next().value);
      // Sus lienzos son memoria de Skia que V8 no ve: sin esto tarda en soltarla.
      if (globalThis.gc) globalThis.gc();
    }
  }
  scenes.set(key, loaded); // la última usada, al final
  return loaded;
}

function send(header, buffers = []) {
  process.stdout.write(JSON.stringify(header) + "\n");
  for (const buffer of buffers) process.stdout.write(buffer);
}

// Lo que devuelve exportFrame (`{w, h, rgba}`), solo o dentro de un recuadro
// (`{x, y, u}`), cuenta como imagen. Cualquier función puede devolver una o una
// lista: los fotogramas de renderFrames, el podio de los caballos...
const isImage = (v) => v && typeof v === "object" && v.rgba instanceof Uint8ClampedArray;
const asPatch = (v) => (isImage(v) ? { x: 0, y: 0, u: v } : v && isImage(v.u) ? v : null);

async function handle(request) {
  const { context, parse } = scene(request.scene);
  if (request.eval) return send({ ok: true, value: await vm.runInContext(request.call, context) });
  // Los argumentos se leen dentro del contexto: así son objetos de la escena y
  // `instanceof Array` sigue valiendo allí.
  const args = parse(JSON.stringify(request.args || []));
  const result = await context[request.call](...args);
  const list = Array.isArray(result) ? result : [result];
  const patches = list.map(asPatch);
  if (!list.length || patches.some((p) => p === null)) return send({ ok: true });
  const frames = patches.map((p) => [p.x, p.y, p.u.w, p.u.h]);
  const buffers = patches.map((p) => Buffer.from(p.u.rgba.buffer, p.u.rgba.byteOffset, p.u.rgba.byteLength));
  send({ ok: true, frames }, buffers);
}

// Las peticiones se atienden de una en una y en orden: la escena guarda estado
// entre fotogramas (el recuadro anterior, el marcador).
let queue = Promise.resolve();
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  queue = queue.then(() => handle(JSON.parse(line))).catch((error) => send({ ok: false, error: String(error && error.stack || error) }));
});
send({ ready: true });
