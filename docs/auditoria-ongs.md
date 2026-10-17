# Auditoría de las ONGs (`donar`, 15/10/2026)

Repaso de `bot.services.donations`, `bot.cogs.donations` y la deducción de
`bot.services.taxes.donation_deduction`, con lo que salió y lo que se cambió.

## Lo que está bien

- **Fiscalidad real.** La deducción es la del art. 19.1 de la Ley 49/2002 tras la
  reforma de 2024: el 80 % de los primeros 250 € al año (desde la 8.0.0, unos
  1.202 Y$ a la semana con la vara del IRPF; antes, 2.500 Y$) y el 40 % del resto,
  con el límite del 10 % de la base (art. 69.1 LIRPF). La deducción rebaja la cuota
  de la declaración del lunes y nunca la deja en negativo. El donativo no paga IGIC
  (no hay contraprestación, art. 4 de la Ley 20/1991) ni Donaciones (la ONG no es persona física, art. 3 de la Ley 29/1987). No
  se modela el 45 % de los donativos recurrentes (tres años a la misma entidad): con
  la renta semanal del bot no tendría sentido.
- **Es un sumidero limpio.** Lo donado se queda en el monedero de la ONG (ids
  negativos) y no vuelve a nadie; lo que se recupera sale del Estado y como mucho es
  el 80 % de lo donado, así que donar siempre cuesta. Los monederos de las ONGs no
  salen en `fortunas`, ni en el Patrimonio, ni en las estadísticas de miembros (todas
  esas consultas piden `user_id > 0`).
- No se puede donar 0 ni en negativo (`EconomyService.donate`) ni más de lo que se
  tiene, y `/donar` lleva el aviso de la Renta.

## Lo que se ha cambiado

- **Logros:** había 8 (lo donado en cinco escalones y las cuatro ONGs). La Biblia pide
  al menos una docena por funcionalidad y todas sus familias. Ahora son 19: además,
  donativos por número (10, 100 y 1.000), 10.000 Y$ a cada ONG (uno por ONG),
  Navidad, y secretos con guasa (1 Y$, 666 Y$ y donar todo el saldo). Salen de
  `donation_stats`.
- **Frases:** cada ONG tenía cuatro maneras de gastarse tu dinero; ahora nueve, con
  Waterloo, Koldo, los puros de Bibi, Davos, el tapón pegado y la motosierra de Milei.
- **Mascota:** al donar hablaba de dinero en general (`Moment()`); ahora tiene su
  momento (`Event.DONATE`) con frases propias.
