/*
 * Motor rápido del juego del pico (`picar`), solo para el calibrador.
 *
 * Es una transcripción línea a línea de `simulate` (src/bot/services/picar_physics.py)
 * y de la generación del mundo (src/bot/services/picar_world.py), sin fotogramas ni
 * eventos: lo que necesita `docs/calibrar_picar.py`. Va unas 50 veces más rápido que
 * Python y da los mismos resultados bit a bit:
 *
 * - La física solo usa + - * /, sqrt y floor, que IEEE 754 da exactos, en el mismo orden
 *   que Python. Se compila con -ffp-contract=off (sin FMA, que redondea distinto) y sin
 *   -ffast-math. El % de Python se reproduce con fmod y su ajuste de signo.
 * - El azar es el Mersenne Twister de `random.Random` sembrado igual que Python siembra
 *   un texto: SHA-512 del texto pegado al texto, como entero, en palabras de 32 bits.
 *   randrange, randint, random y uniform siguen el código de CPython.
 *
 * Las constantes, bloques y capas no están aquí: las escribe `docs/picar_rapido.py` en
 * picar_params.h desde el código de Python al compilar, así que no hay dos copias que
 * puedan desviarse. Aun así, `picar_rapido.comprobar` compara este motor con el de
 * Python antes de cada calibración, y el bot comprueba la huella al arrancar.
 */

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "picar_params.h"

/* -- SHA-512 ---------------------------------------------------------------------------- */

static const uint64_t K512[80] = {
    0x428a2f98d728ae22ULL, 0x7137449123ef65cdULL, 0xb5c0fbcfec4d3b2fULL, 0xe9b5dba58189dbbcULL,
    0x3956c25bf348b538ULL, 0x59f111f1b605d019ULL, 0x923f82a4af194f9bULL, 0xab1c5ed5da6d8118ULL,
    0xd807aa98a3030242ULL, 0x12835b0145706fbeULL, 0x243185be4ee4b28cULL, 0x550c7dc3d5ffb4e2ULL,
    0x72be5d74f27b896fULL, 0x80deb1fe3b1696b1ULL, 0x9bdc06a725c71235ULL, 0xc19bf174cf692694ULL,
    0xe49b69c19ef14ad2ULL, 0xefbe4786384f25e3ULL, 0x0fc19dc68b8cd5b5ULL, 0x240ca1cc77ac9c65ULL,
    0x2de92c6f592b0275ULL, 0x4a7484aa6ea6e483ULL, 0x5cb0a9dcbd41fbd4ULL, 0x76f988da831153b5ULL,
    0x983e5152ee66dfabULL, 0xa831c66d2db43210ULL, 0xb00327c898fb213fULL, 0xbf597fc7beef0ee4ULL,
    0xc6e00bf33da88fc2ULL, 0xd5a79147930aa725ULL, 0x06ca6351e003826fULL, 0x142929670a0e6e70ULL,
    0x27b70a8546d22ffcULL, 0x2e1b21385c26c926ULL, 0x4d2c6dfc5ac42aedULL, 0x53380d139d95b3dfULL,
    0x650a73548baf63deULL, 0x766a0abb3c77b2a8ULL, 0x81c2c92e47edaee6ULL, 0x92722c851482353bULL,
    0xa2bfe8a14cf10364ULL, 0xa81a664bbc423001ULL, 0xc24b8b70d0f89791ULL, 0xc76c51a30654be30ULL,
    0xd192e819d6ef5218ULL, 0xd69906245565a910ULL, 0xf40e35855771202aULL, 0x106aa07032bbd1b8ULL,
    0x19a4c116b8d2d0c8ULL, 0x1e376c085141ab53ULL, 0x2748774cdf8eeb99ULL, 0x34b0bcb5e19b48a8ULL,
    0x391c0cb3c5c95a63ULL, 0x4ed8aa4ae3418acbULL, 0x5b9cca4f7763e373ULL, 0x682e6ff3d6b2b8a3ULL,
    0x748f82ee5defb2fcULL, 0x78a5636f43172f60ULL, 0x84c87814a1f0ab72ULL, 0x8cc702081a6439ecULL,
    0x90befffa23631e28ULL, 0xa4506cebde82bde9ULL, 0xbef9a3f7b2c67915ULL, 0xc67178f2e372532bULL,
    0xca273eceea26619cULL, 0xd186b8c721c0c207ULL, 0xeada7dd6cde0eb1eULL, 0xf57d4f7fee6ed178ULL,
    0x06f067aa72176fbaULL, 0x0a637dc5a2c898a6ULL, 0x113f9804bef90daeULL, 0x1b710b35131c471bULL,
    0x28db77f523047d84ULL, 0x32caab7b40c72493ULL, 0x3c9ebe0a15c9bebcULL, 0x431d67c49c100d4cULL,
    0x4cc5d4becb3e42b6ULL, 0x597f299cfc657e2aULL, 0x5fcb6fab3ad6faecULL, 0x6c44198c4a475817ULL,
};

#define ROTR(x, n) (((x) >> (n)) | ((x) << (64 - (n))))

static void sha512_block(uint64_t h[8], const unsigned char *p) {
    uint64_t w[80];
    for (int i = 0; i < 16; i++) {
        w[i] = 0;
        for (int b = 0; b < 8; b++) w[i] = (w[i] << 8) | p[i * 8 + b];
    }
    for (int i = 16; i < 80; i++) {
        uint64_t s0 = ROTR(w[i - 15], 1) ^ ROTR(w[i - 15], 8) ^ (w[i - 15] >> 7);
        uint64_t s1 = ROTR(w[i - 2], 19) ^ ROTR(w[i - 2], 61) ^ (w[i - 2] >> 6);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint64_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
    for (int i = 0; i < 80; i++) {
        uint64_t S1 = ROTR(e, 14) ^ ROTR(e, 18) ^ ROTR(e, 41);
        uint64_t ch = (e & f) ^ (~e & g);
        uint64_t t1 = hh + S1 + ch + K512[i] + w[i];
        uint64_t S0 = ROTR(a, 28) ^ ROTR(a, 34) ^ ROTR(a, 39);
        uint64_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint64_t t2 = S0 + maj;
        hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}

static void sha512(const unsigned char *msg, size_t len, unsigned char out[64]) {
    uint64_t h[8] = {
        0x6a09e667f3bcc908ULL, 0xbb67ae8584caa73bULL, 0x3c6ef372fe94f82bULL, 0xa54ff53a5f1d36f1ULL,
        0x510e527fade682d1ULL, 0x9b05688c2b3e6c1fULL, 0x1f83d9abfb41bd6bULL, 0x5be0cd19137e2179ULL,
    };
    unsigned char block[256];
    size_t full = len / 128;
    for (size_t i = 0; i < full; i++) sha512_block(h, msg + i * 128);
    size_t rest = len - full * 128;
    memset(block, 0, sizeof block);
    memcpy(block, msg + full * 128, rest);
    block[rest] = 0x80;
    size_t total = rest + 1 + 16 <= 128 ? 128 : 256;
    uint64_t bits = (uint64_t)len * 8;
    for (int b = 0; b < 8; b++) block[total - 1 - b] = (unsigned char)(bits >> (8 * b));
    for (size_t i = 0; i < total; i += 128) sha512_block(h, block + i);
    for (int i = 0; i < 8; i++)
        for (int b = 0; b < 8; b++) out[i * 8 + b] = (unsigned char)(h[i] >> (56 - 8 * b));
}

/* -- Mersenne Twister de CPython -------------------------------------------------------- */

#define MT_N 624
#define MT_M 397

typedef struct {
    uint32_t mt[MT_N];
    int index;
} MT;

static void init_genrand(MT *m, uint32_t s) {
    m->mt[0] = s;
    for (int i = 1; i < MT_N; i++)
        m->mt[i] = (1812433253U * (m->mt[i - 1] ^ (m->mt[i - 1] >> 30)) + (uint32_t)i);
    m->index = MT_N;
}

static void init_by_array(MT *m, const uint32_t *key, size_t len) {
    init_genrand(m, 19650218U);
    size_t i = 1, j = 0;
    size_t k = MT_N > len ? MT_N : len;
    for (; k; k--) {
        m->mt[i] = (m->mt[i] ^ ((m->mt[i - 1] ^ (m->mt[i - 1] >> 30)) * 1664525U)) + key[j] +
                   (uint32_t)j;
        i++; j++;
        if (i >= MT_N) { m->mt[0] = m->mt[MT_N - 1]; i = 1; }
        if (j >= len) j = 0;
    }
    for (k = MT_N - 1; k; k--) {
        m->mt[i] = (m->mt[i] ^ ((m->mt[i - 1] ^ (m->mt[i - 1] >> 30)) * 1566083941U)) - (uint32_t)i;
        i++;
        if (i >= MT_N) { m->mt[0] = m->mt[MT_N - 1]; i = 1; }
    }
    m->mt[0] = 0x80000000U;
}

static uint32_t genrand(MT *m) {
    static const uint32_t mag01[2] = {0x0U, 0x9908b0dfU};
    uint32_t y;
    if (m->index >= MT_N) {
        int kk;
        for (kk = 0; kk < MT_N - MT_M; kk++) {
            y = (m->mt[kk] & 0x80000000U) | (m->mt[kk + 1] & 0x7fffffffU);
            m->mt[kk] = m->mt[kk + MT_M] ^ (y >> 1) ^ mag01[y & 0x1U];
        }
        for (; kk < MT_N - 1; kk++) {
            y = (m->mt[kk] & 0x80000000U) | (m->mt[kk + 1] & 0x7fffffffU);
            m->mt[kk] = m->mt[kk + (MT_M - MT_N)] ^ (y >> 1) ^ mag01[y & 0x1U];
        }
        y = (m->mt[MT_N - 1] & 0x80000000U) | (m->mt[0] & 0x7fffffffU);
        m->mt[MT_N - 1] = m->mt[MT_M - 1] ^ (y >> 1) ^ mag01[y & 0x1U];
        m->index = 0;
    }
    y = m->mt[m->index++];
    y ^= (y >> 11);
    y ^= (y << 7) & 0x9d2c5680U;
    y ^= (y << 15) & 0xefc60000U;
    y ^= (y >> 18);
    return y;
}

/* `random.Random(texto)`: el entero big-endian de texto + sha512(texto), en palabras de
   32 bits empezando por la menos significativa. */
static void seed_text(MT *m, const char *text) {
    size_t n = strlen(text);
    unsigned char bytes[256 + 64];
    memcpy(bytes, text, n);
    sha512((const unsigned char *)text, n, bytes + n);
    size_t total = n + 64;
    size_t start = 0;
    while (start < total && bytes[start] == 0) start++;
    size_t used = total - start;
    int top_bits = 0;
    if (used) {
        unsigned char first = bytes[start];
        while (first) { top_bits++; first >>= 1; }
    }
    size_t bitlen = used ? (used - 1) * 8 + (size_t)top_bits : 0;
    size_t words = bitlen ? (bitlen - 1) / 32 + 1 : 1;
    uint32_t key[128];
    memset(key, 0, sizeof key);
    for (size_t i = 0; i < used; i++) {
        size_t le = i;                       /* byte i desde el final */
        unsigned char v = bytes[total - 1 - i];
        key[le / 4] |= (uint32_t)v << (8 * (le % 4));
    }
    init_by_array(m, key, words);
}

static int bit_length(uint32_t n) {
    int k = 0;
    while (n) { k++; n >>= 1; }
    return k;
}

static uint32_t getrandbits(MT *m, int k) { return genrand(m) >> (32 - k); }

static uint32_t randbelow(MT *m, uint32_t n) {
    int k = bit_length(n);
    uint32_t r = getrandbits(m, k);
    while (r >= n) r = getrandbits(m, k);
    return r;
}

static long long randint(MT *m, long long a, long long b) {
    return a + (long long)randbelow(m, (uint32_t)(b - a + 1));
}

static double rnd(MT *m) {
    uint32_t a = genrand(m) >> 5, b = genrand(m) >> 6;
    return (a * 67108864.0 + b) * (1.0 / 9007199254740992.0);
}

static double uniform(MT *m, double a, double b) { return a + (b - a) * rnd(m); }

/* Para la prueba del generador: los `count` primeros enteros de Random(texto). */
void picar_mt_sample(const char *text, uint32_t *out, int count) {
    MT m;
    seed_text(&m, text);
    for (int i = 0; i < count; i++) out[i] = genrand(&m);
}

/* -- Mundo ------------------------------------------------------------------------------ */

#define ROWS (DRAGON_ROW + CHUNK)
#define REGIONS (DRAGON_ROW / RAVINE_REGION + 2)

typedef struct {
    int has;
    int top, width, height;
    int cols[RAVINE_ROWS_MAX];
} Ravine;

typedef struct {
    long long seed;
    unsigned char cells[ROWS][COLUMNS];
    int hp[ROWS][COLUMNS];
    unsigned char loaded[ROWS / CHUNK + 1];
    unsigned char ravine_done[REGIONS];
    Ravine ravine[REGIONS];
} World;

static int layer_at(int row) {
    int index = 0;
    for (int i = 0; i < NLAYERS; i++)
        if (LAYER_TOP[i] <= row) index = i;
    return index;
}

static const Ravine *ravine_of(World *w, int region) {
    static const Ravine none = {0};
    if (region < 0) return &none;
    int first = region * RAVINE_REGION;
    if (first >= DRAGON_ROW) return &none;
    Ravine *r = &w->ravine[region];
    if (w->ravine_done[region]) return r;
    w->ravine_done[region] = 1;
    r->has = 0;
    char text[96];
    snprintf(text, sizeof text, "picar:barranco:%lld:%d", w->seed, region);
    MT m;
    seed_text(&m, text);
    if ((int)randbelow(&m, 1000) >= LAYER_RAVINE[layer_at(first + RAVINE_REGION / 2)]) return r;
    int top = first + (int)randbelow(&m, RAVINE_REGION);
    if (top < 1) top = 1;
    int height = (int)randint(&m, RAVINE_ROWS_MIN, RAVINE_ROWS_MAX);
    if (height > DRAGON_ROW - top) height = DRAGON_ROW - top;
    int width = (int)randint(&m, 1, 2);
    int col = (int)randbelow(&m, (uint32_t)(COLUMNS - width + 1));
    for (int i = 0; i < height; i++) {
        r->cols[i] = col;
        int step = (int)randbelow(&m, 6);
        if (step == 0 && col > 0) col -= 1;
        else if (step == 1 && col < COLUMNS - width) col += 1;
    }
    if (height > 0) { r->has = 1; r->top = top; r->width = width; r->height = height; }
    return r;
}

static void load_chunk(World *w, int chunk) {
    w->loaded[chunk] = 1;
    int first = chunk * CHUNK;
    char text[96];
    snprintf(text, sizeof text, "picar:mundo:%lld:%d", w->seed, chunk);
    MT m;
    seed_text(&m, text);
    for (int i = 0; i < CHUNK; i++) {
        int row = first + i;
        if (row >= DRAGON_ROW) {
            for (int c = 0; c < COLUMNS; c++) w->cells[row][c] = 0;
            continue;
        }
        int L = layer_at(row);
        for (int c = 0; c < COLUMNS; c++) {
            uint32_t pick = randbelow(&m, (uint32_t)TABLE_TOTAL[L]);
            int j = 0;
            /* bisect_right(acumulados, pick) */
            while (j < TABLE_LEN[L] && TABLE_CUM[L][j] <= (long long)pick) j++;
            w->cells[row][c] = (unsigned char)TABLE_ID[L][j];
        }
    }
    if (first < DRAGON_ROW) {
        int L = layer_at(first);
        int low = LAYER_CAVES_LO[L], high = LAYER_CAVES_HI[L];
        int caves = high ? (int)randint(&m, low, high) : 0;
        for (int n = 0; n < caves; n++) {
            int cx = (int)randbelow(&m, COLUMNS), cy = (int)randbelow(&m, CHUNK);
            int rx = (int)randint(&m, 1, 3), ry = (int)randint(&m, 1, 2);
            int limit = rx * rx * ry * ry;
            for (int dy = -ry; dy <= ry; dy++) {
                int r = cy + dy;
                if (r < 0 || r >= CHUNK) continue;
                for (int dx = -rx; dx <= rx; dx++) {
                    int c = cx + dx;
                    if (c >= 0 && c < COLUMNS && dx * dx * ry * ry + dy * dy * rx * rx <= limit)
                        w->cells[first + r][c] = 0;
                }
            }
        }
        int region = first / RAVINE_REGION;
        for (int k = -1; k <= 0; k++) {
            const Ravine *rv = ravine_of(w, region + k);
            if (!rv->has) continue;
            for (int i = 0; i < rv->height; i++) {
                int r = rv->top + i - first;
                if (r >= 0 && r < CHUNK)
                    for (int c = rv->cols[i]; c < rv->cols[i] + rv->width; c++)
                        w->cells[first + r][c] = 0;
            }
        }
    }
    for (int i = 0; i < CHUNK; i++)
        for (int c = 0; c < COLUMNS; c++)
            w->hp[first + i][c] = BLOCK_RESISTANCE[w->cells[first + i][c]];
}

static inline unsigned char *row_of(World *w, int row) {
    int chunk = row / CHUNK;
    if (!w->loaded[chunk]) load_chunk(w, chunk);
    return w->cells[row];
}

static inline int *hp_of(World *w, int row) {
    row_of(w, row);
    return w->hp[row];
}

/* -- Física ----------------------------------------------------------------------------- */

typedef struct {
    long long ticks, life, max_row, dragon, timeout, hits, life_gained, explosions;
    long long longest_chain, nudges, stalls, multiplier;
    double x, y, angle;
    long long broken[NBLOCKS];
} Result;

typedef struct { double pen; int r, c; double nx, ny; } Contact;

static Contact deepest(World *w, double x, double y) {
    Contact best = {0.0, 0, 0, 0.0, 0.0};
    int r0 = (int)floor(y - RADIUS), r1 = (int)floor(y + RADIUS);
    int c0 = (int)floor(x - RADIUS), c1 = (int)floor(x + RADIUS);
    if (c0 < 0) c0 = 0;
    if (c1 > COLUMNS - 1) c1 = COLUMNS - 1;
    for (int r = r0 < 0 ? 0 : r0; r <= r1; r++) {
        if (r >= DRAGON_ROW) continue;   /* aire, como las filas del dragón en Python */
        unsigned char *cells = row_of(w, r);
        for (int c = c0; c <= c1; c++) {
            if (cells[c] == 0) continue;
            double px = x < c ? (double)c : (x > c + 1 ? (double)(c + 1) : x);
            double py = y < r ? (double)r : (y > r + 1 ? (double)(r + 1) : y);
            double dx = x - px, dy = y - py;
            double d2 = dx * dx + dy * dy;
            if (d2 >= RADIUS * RADIUS) continue;
            double d = sqrt(d2);
            double pen = RADIUS - d;
            if (pen > best.pen) {
                best.pen = pen; best.r = r; best.c = c; best.nx = dx / d; best.ny = dy / d;
            }
        }
    }
    return best;
}

static double py_mod(double a, double b) {
    double mod = fmod(a, b);
    if (mod != 0.0) {
        if ((b < 0) != (mod < 0)) mod += b;
    } else {
        mod = copysign(0.0, b);
    }
    return mod;
}

typedef struct { int r, c, radius; } Centre;
typedef struct { int r, c, block; } Prize;

#define MAX_QUEUE 4096

int picar_simulate(long long seed, long long life0, long long power, double bounce, Result *out) {
    World *w = calloc(1, sizeof(World));
    if (!w) return -1;
    w->seed = seed;
    memset(out, 0, sizeof *out);
    out->multiplier = 1;
    MT rng;
    char text[64];
    snprintf(text, sizeof text, "picar:fisica:%lld", seed);
    seed_text(&rng, text);

    long long life = life0;
    double x = COLUMNS / 2.0 + uniform(&rng, -0.3, 0.3);
    double y = -2.0;
    double vx = uniform(&rng, -1.5, 1.5), vy = 0.0;
    double angle = 0.0, spin = 0.0;
    double gravity_step = GRAVITY * DT;
    long long max_row = -1, last_progress = 0;
    static Centre centres[MAX_QUEUE];
    static Prize prizes[MAX_QUEUE];
    static int queue_r[MAX_QUEUE], queue_c[MAX_QUEUE];

    long long tick = 0;
    for (;;) {
        tick += 1;
        if (tick > MAX_TICKS) { out->timeout = 1; tick -= 1; break; }

        vy += gravity_step;
        double speed2 = vx * vx + vy * vy;
        if (speed2 > MAX_SPEED * MAX_SPEED) {
            double f = MAX_SPEED / sqrt(speed2);
            vx = vx * f; vy = vy * f;
        }
        double px = x, py = y;
        x += vx * DT;
        y += vy * DT;

        if (x < RADIUS) { x = RADIUS; vx = -vx * WALL_BOUNCE; }
        else if (x > COLUMNS - RADIUS) { x = COLUMNS - RADIUS; vx = -vx * WALL_BOUNCE; }

        if (y + RADIUS >= DRAGON_ROW) {
            y = DRAGON_ROW - RADIUS;
            out->dragon = 1;
            max_row = DRAGON_ROW;
            break;
        }

        int dead = 0, resolved = 0;
        for (int attempt = 0; attempt < MAX_RESOLVE; attempt++) {
            Contact k = deepest(w, x, y);
            double pen = k.pen, nx = k.nx, ny = k.ny;
            int r = k.r, c = k.c;
            if (pen == 0.0) { resolved = 1; break; }
            double vn = vx * nx + vy * ny;
            if (vn >= 0.0 || dead) {
                x += nx * (pen + SLOP);
                y += ny * (pen + SLOP);
                continue;
            }
            int floor_hit = ny < FLOOR_NORMAL;
            if (!floor_hit && -vn < HIT_SPEED) {
                x += nx * (pen + SLOP);
                y += ny * (pen + SLOP);
                vx -= vn * nx;
                vy -= vn * ny;
                continue;
            }

            out->hits += 1;
            unsigned char *cells = row_of(w, r);
            int *hps = hp_of(w, r);
            int block = cells[c];
            hps[c] -= (int)power;
            life -= BLOCK_HARDNESS[block];
            double tx = -ny, ty = nx;
            double vt = vx * tx + vy * ty;
            spin = spin * 0.5 + vt / RADIUS * 0.5;
            if (hps[c] <= 0) {
                cells[c] = 0; hps[c] = 0;
                out->broken[block] += 1;
                double outb = -vn * bounce * BREAK_BOUNCE;
                if (floor_hit && outb < MIN_BOUNCE * BREAK_BOUNCE) outb = MIN_BOUNCE * BREAK_BOUNCE;
                vt = vt * FRICTION + uniform(&rng, -NOISE, NOISE) * -vn;
                vx = nx * outb + tx * vt;
                vy = ny * outb + ty * vt;
                int ncentres = 0, nprizes = 0;
                if (BLOCK_KIND[block] == KIND_TNT) {
                    int head = 0, tail = 0;
                    queue_r[tail] = r; queue_c[tail] = c; tail++;
                    while (head < tail) {
                        int qr = queue_r[head], qc = queue_c[head]; head++;
                        int radius = TNT_RADII[randbelow(&rng, NTNT_RADII)];
                        centres[ncentres].r = qr; centres[ncentres].c = qc;
                        centres[ncentres].radius = radius; ncentres++;
                        int rlo = qr - radius < 0 ? 0 : qr - radius;
                        int rhi = qr + radius > DRAGON_ROW - 1 ? DRAGON_ROW - 1 : qr + radius;
                        int clo = qc - radius < 0 ? 0 : qc - radius;
                        int chi = qc + radius > COLUMNS - 1 ? COLUMNS - 1 : qc + radius;
                        for (int rr = rlo; rr <= rhi; rr++) {
                            unsigned char *row = row_of(w, rr);
                            int *hrow = hp_of(w, rr);
                            for (int cc = clo; cc <= chi; cc++) {
                                int b = row[cc];
                                int dr = rr - qr, dc = cc - qc;
                                if (b == 0 || dr * dr + dc * dc > radius * radius) continue;
                                row[cc] = 0; hrow[cc] = 0;
                                out->broken[b] += 1;
                                if (BLOCK_KIND[b] == KIND_TNT) {
                                    queue_r[tail] = rr; queue_c[tail] = cc; tail++;
                                } else if (BLOCK_KIND[b] != KIND_PLAIN) {
                                    prizes[nprizes].r = rr; prizes[nprizes].c = cc;
                                    prizes[nprizes].block = b; nprizes++;
                                }
                            }
                        }
                    }
                } else if (BLOCK_KIND[block] != KIND_PLAIN) {
                    prizes[0].r = r; prizes[0].c = c; prizes[0].block = block; nprizes = 1;
                }
                for (int i = 0; i < nprizes; i++) {
                    int b = prizes[i].block;
                    if (BLOCK_KIND[b] == KIND_MULT) { out->multiplier *= BLOCK_MULT[b]; continue; }
                    if (BLOCK_KIND[b] == KIND_MYSTERY) {
                        out->multiplier *= MYSTERY_MULTS[randint(&rng, 0, NMYSTERY - 1)];
                        continue;
                    }
                    long long gained;
                    if (BLOCK_KIND[b] == KIND_CRAFT) {
                        gained = life0 - life;
                        if (gained < 0) gained = 0;
                    } else {
                        gained = life0 * randint(&rng, BLOCK_LIFE_LO[b], BLOCK_LIFE_HI[b]) / 100;
                    }
                    life += gained;
                    out->life_gained += gained;
                }
                if (ncentres) {
                    out->explosions += ncentres;
                    if (ncentres > out->longest_chain) out->longest_chain = ncentres;
                    for (int i = 0; i < ncentres; i++) {
                        double dx = x - (centres[i].c + 0.5), dy = y - (centres[i].r + 0.5);
                        double d2 = dx * dx + dy * dy;
                        double reach = centres[i].radius + 1.0;
                        if (0.0 < d2 && d2 < reach * reach) {
                            double d = sqrt(d2);
                            double push = TNT_KICK * (1 - d / reach);
                            vx += dx / d * push;
                            vy += dy / d * push - push * 0.5;
                        }
                    }
                }
            } else {
                double outv = -vn * bounce;
                if (floor_hit && outv < MIN_BOUNCE) outv = MIN_BOUNCE;
                vt = vt * FRICTION + uniform(&rng, -NOISE, NOISE) * -vn;
                vx = nx * outv + tx * vt;
                vy = ny * outv + ty * vt;
                x += nx * (pen + SLOP);
                y += ny * (pen + SLOP);
            }
            if (life <= 0) dead = 1;
        }
        if (!resolved) {
            /* El for/else de Python: no salió de los bloques en MAX_RESOLVE intentos. */
            if (deepest(w, x, y).pen > 0.0) {
                x = px; y = py; vx = 0.0; vy = 0.0;
                out->stalls += 1;
            }
        }

        angle = py_mod(angle + spin * DT * 57.29577951308232, 360.0);
        long long row = (long long)floor(y);
        if (row > max_row) {
            max_row = row; last_progress = tick;
        } else if (tick - last_progress >= STUCK_TICKS) {
            last_progress = tick;
            out->nudges += 1;
            vx += rnd(&rng) < 0.5 ? NUDGE : -NUDGE;
            vy -= NUDGE;
        }
        if (dead) break;
    }

    out->ticks = tick;
    out->life = life;
    out->x = x; out->y = y; out->angle = angle;
    out->max_row = max_row < 0 ? 0 : max_row;
    free(w);
    return 0;
}
