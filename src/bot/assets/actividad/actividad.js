// La Activity de Discord (bot/cogs/actividad.py): cara o cruz con los yapdollars del servidor.
//
// El bot lo decide todo (azar, dinero, textos y botones) y manda los fotogramas de la
// escena de la moneda (assets/moneda/escena.html), los mismos con los que pinta el GIF de
// `moneda`. Aquí solo se pide permiso a Discord, se pinta cada fotograma con la escena
// en un marco oculto, se copia al lienzo visible a su ritmo y se ponen los botones.
import { DiscordSDK } from "/assets/actividad/sdk.js";

const $ = (id) => document.getElementById(id);
const sdk = new DiscordSDK(document.querySelector('meta[name="client-id"]').content);
const table = $("mesa").getContext("2d");
let token = null;
let busy = false;
let last = null; // la última respuesta del bot: apuesta y saldo para ½ y ×2

// -- Permiso ------------------------------------------------------------------------

async function request(path, { method = "GET", body } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { method, headers, body: body && JSON.stringify(body) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw Object.assign(new Error(data.error || "Algo ha fallado."), { status: response.status });
  }
  return data;
}

// Discord da un código y el bot lo cambia por el token (con el secreto, que aquí no está).
async function login() {
  await sdk.ready();
  const { code } = await sdk.commands.authorize({
    client_id: sdk.clientId,
    response_type: "code",
    state: "",
    prompt: "none",
    scope: ["identify"],
  });
  const data = await request("/api/token", {
    method: "POST",
    body: { code, instance_id: sdk.instanceId },
  });
  token = data.access_token;
}

// La sesión vive en la memoria del bot: tras un reinicio llega un 401 y se pide permiso otra vez.
async function api(method, body) {
  try {
    return await request("/api/moneda", { method, body });
  } catch (error) {
    if (error.status !== 401) throw error;
    await login();
    return request("/api/moneda", { method, body });
  }
}

// -- Escena -------------------------------------------------------------------------

const scene = new Promise((resolve, reject) => {
  const frame = document.createElement("iframe");
  frame.src = "/assets/moneda/escena.html";
  frame.className = "escena";
  frame.tabIndex = -1;
  frame.setAttribute("aria-hidden", "true");
  frame.addEventListener(
    "load",
    () => {
      const win = frame.contentWindow;
      // Cada recuadro llega como lienzo, sin pasar por PNG como en el NAS, y se pega tal cual.
      win.exportFrame = (canvas) => {
        const copy = document.createElement("canvas");
        copy.width = canvas.width;
        copy.height = canvas.height;
        copy.getContext("2d").drawImage(canvas, 0, 0);
        return copy;
      };
      win.loadFonts().then(() => resolve(win), reject);
    },
    { once: true },
  );
  document.body.append(frame);
});
let sceneMeta = null;
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

// La escena devuelve el recuadro que cambia respecto al anterior: el lienzo visible
// siempre tiene lo último que pintó, así que basta con pegarlo encima.
async function animate(data) {
  const win = await scene;
  const meta = JSON.stringify(data.meta);
  if (meta !== sceneMeta) {
    await win.setup(data.meta);
    sceneMeta = meta;
  }
  for (let i = 0; i < data.states.length; i++) {
    const start = performance.now();
    const [patch] = win.renderFrames([data.states[i]], i);
    table.drawImage(patch.u, patch.x, patch.y);
    if (i + 1 < data.states.length) await sleep(data.ms - (performance.now() - start));
  }
}

// -- Textos y botones ---------------------------------------------------------------

// Los textos vienen en el Markdown de Discord. Se usa lo poco que traen (títulos,
// letra pequeña, negritas y `código`) y todo entra como texto, nunca como HTML.
function markdown(text) {
  const box = document.createDocumentFragment();
  for (const line of text.split("\n")) {
    let tag = "p";
    let body = line;
    if (line.startsWith("# ")) [tag, body] = ["h2", line.slice(2)];
    else if (line.startsWith("-# ")) [tag, body] = ["small", line.slice(3)];
    const element = document.createElement(tag);
    for (const part of body.split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
      if (part.startsWith("**") && part.length > 4) {
        element.append(Object.assign(document.createElement("b"), { textContent: part.slice(2, -2) }));
      } else if (part.startsWith("`") && part.length > 2) {
        element.append(Object.assign(document.createElement("code"), { textContent: part.slice(1, -1) }));
      } else if (part) {
        element.append(part);
      }
    }
    box.append(element);
  }
  return box;
}

const say = (text) => $("texto").replaceChildren(markdown(text));

function setBusy(value) {
  busy = value;
  for (const control of document.querySelectorAll("#botones button, #botones input")) {
    control.disabled = value;
  }
}

function show(data) {
  last = data;
  $("titulo").textContent = data.titulo;
  say(data.texto);
  const { cara, cruz, cobrar } = data.botones;
  $("cara").textContent = cara;
  $("cruz").textContent = cruz;
  $("cobrar").textContent = cobrar ?? "";
  $("cobrar").hidden = !cobrar;
  // Con una racha en juego solo se lanza o se cobra, como en la mesa de Discord.
  $("apuestas").hidden = Boolean(cobrar);
  $("botones").hidden = false;
}

// El texto final y los botones nuevos delatan el resultado: van cuando acaba la animación.
async function act(body) {
  if (busy) return;
  setBusy(true);
  $("aviso").textContent = "";
  try {
    const data = await api("POST", body);
    if (data.volando) say(data.volando);
    await animate(data);
    show(data);
  } catch (error) {
    $("aviso").textContent = error.message;
  } finally {
    setBusy(false);
  }
}

$("cara").addEventListener("click", () => act({ accion: "cara" }));
$("cruz").addEventListener("click", () => act({ accion: "cruz" }));
$("cobrar").addEventListener("click", () => act({ accion: "cobrar" }));
$("mitad").addEventListener("click", () =>
  act({ accion: "apuesta", cantidad: String(Math.max(1, Math.floor(last.apuesta / 2))) }),
);
$("doble").addEventListener("click", () =>
  act({ accion: "apuesta", cantidad: String(Math.max(1, Math.min(last.apuesta * 2, last.saldo))) }),
);
$("todo").addEventListener("click", () => act({ accion: "apuesta", cantidad: "all" }));
$("cantidad").addEventListener("submit", (event) => {
  event.preventDefault();
  const amount = event.target.elements.cantidad.value.trim();
  event.target.reset();
  if (amount) act({ accion: "apuesta", cantidad: amount });
});

try {
  await login();
  const data = await api("GET");
  await animate(data);
  show(data);
} catch (error) {
  say(error.message || "No se ha podido abrir la mesa.");
}
