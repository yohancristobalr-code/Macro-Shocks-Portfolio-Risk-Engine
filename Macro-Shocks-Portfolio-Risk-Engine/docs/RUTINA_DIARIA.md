# Hoja de ruta operativa

Qué hacer con el motor, con qué frecuencia y cómo interpretar cada resultado. Para la instalación y el detalle de cada comando, consulta [GUIA_USO.md](GUIA_USO.md).

Todos los comandos se ejecutan desde la carpeta del proyecto:

```powershell
cd $HOME\Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine
```

## Resumen

| Cuándo | Comando | Tiempo | Para qué |
|---|---|---|---|
| **Antes de operar** | `report --open` o doble clic en "Informe Macro" | 1–3 min | Todo en un informe ejecutivo con gráficas |
| **Cada día de mercado** | `daily --quick` | 1 min | Régimen y alertas |
| **Días de evento** (Fed, BCE…) | `analyze ... --portfolio portfolio.csv --sectors --inflation` | 5 min | Riesgo del anuncio para tu cartera |
| **Antes de cada IPC** (≈ día 10–15) | `nowcast` | 1 min | Qué inflación esperar y qué la empuja |
| **Antes del PMI flash** (≈ día 20–23) | `macro --quick` | 1 min | Dirección probable del PMI y de sus precios |
| **Cada semana** (viernes) | `daily` (completo) y `macro` | 5 min | Evidencia sectorial del régimen, escenario macro y cuadrante |
| **Cada mes** | `optimize --max-drawdown <tu límite>` | 3 min | Revisar los pesos objetivo |
| **Cada trimestre** | `git pull`, tests, cambiar la clave | 10 min | Mantenimiento |

---

## 1. Cada día de mercado (≈1 minuto)

### Paso 1: ejecutar

```powershell
python -m macro_shock daily --quick
```

Los datos de FRED corresponden al **cierre del día hábil anterior**, así que basta con ejecutarlo por la mañana. También puedes [automatizarlo](#6-automatizar-el-informe-diario).

### Paso 2: leer en este orden

1. **`ALERTS`**: lo único que puede exigir hacer algo.
2. **`Regime`**: el contexto de riesgo. **`Macro`**: el escenario que descuenta el mercado.
3. **Los datos**: 10Y, curva, crédito, VIX, S&P 500 y bitcoin, con el valor de la ejecución anterior.

### Paso 3: decidir

| Lo que ves | Qué significa | Qué hacer |
|---|---|---|
| `ALERTS: none` | Nada relevante cambió | **Nada.** Es el resultado más frecuente, y lo correcto es no operar |
| `REGIME CHANGE` | El mercado pasó a otro estado de vulnerabilidad | Sigue la lista de pasos del informe (sección 1.1) |
| `VIX up` o `HY spread up`, sin cambio de régimen | La tensión sube, pero aún no cambia el régimen | Vigila. Si hay un evento cerca, haz la prueba de estrés (sección 2) |
| `below its 52-week high` | El S&P 500 está en corrección (−10% o peor) | Comprueba que tu cartera sigue dentro de tu límite de caída. **No vendas en pánico**: tras la tensión, el mes siguiente suele traer rebote (ver 1.2) |
| `WTI crude up`, `Diesel, retail up`… | La energía se disparó en 30 días | Ejecuta `nowcast` para ver cuánto empuja al próximo IPC. Un shock de energía hace más probable una Fed dura |
| `MACRO QUADRANT CHANGE` | Los datos de crecimiento e inflación cambiaron de cuadrante | Ejecuta `macro` y lee los sectores `**` y `*` del nuevo cuadrante. Es un cambio lento (datos mensuales): confírmalo durante varias semanas antes de actuar |

### 1.1 Si cambia el régimen

1. Ejecuta `python -m macro_shock sector-study` y lee los resultados `**` y `*` del nuevo régimen.
2. Ejecuta `python -m macro_shock optimize --max-drawdown <tu límite>`: la inclinación por régimen puede cambiar los pesos.
3. Haz la prueba de estrés de tu cartera: `analyze ... --data fred --portfolio portfolio.csv`.
4. **Cambia posiciones solo si la evidencia es `**` o `*`**, nunca por el movimiento de un solo día. Un cambio de régimen que se revierte al día siguiente no es una señal.

### 1.2 Qué significa cada régimen

Con los datos de FRED, el motor distingue cuatro regímenes. El de **crisis** es posible gracias a los índices de estrés financiero de la Fed (`STLFSI4` y `NFCI`), que aportan los componentes de liquidez y financiación.

| Régimen | Mercado | Evidencia histórica desde 1990 (ejecución del 22 de octubre de 2026) |
|---|---|---|
| `risk_on_expansion` | Tranquilo: VIX bajo, crédito barato | Value `*` y momentum `*` favorecidos; consumo duradero, retail y pequeñas empresas `*` rezagados |
| `fragile_risk_on` | Calma aparente, con algo de tensión | **Momentum `**` favorecido** (el único resultado robusto); telecom `*` rezagado |
| `risk_off_correction` | Tensión: VIX alto y/o crédito caro | Ningún resultado claro |
| `crisis` | Pánico: VIX extremo, crédito muy caro y estrés financiero en máximos históricos (2008, 2020) | Tecnología `*` favorecida (rebote); consumo básico y materiales `*` rezagados; **momentum `*` muy negativo (−3.6% al mes)** |

⚠️ **Regla del momentum:** la inclinación hacia momentum es la mejor apuesta en `fragile_risk_on`, pero **en una crisis el momentum se hunde**. Tras un pánico, las acciones que más cayeron son las que más rebotan. **Si el régimen pasa a `crisis`, retira la inclinación hacia momentum.** Con solo 29 meses de crisis desde 1990, es un resultado `*` (coherente), no definitivo.

Esta tabla es una foto de octubre de 2026. **La referencia actualizada es siempre la salida de `sector-study`**, que se recalcula con cada ejecución.

---

## 2. Días de evento (Fed, BCE, etc.)

Fechas de la Fed: [calendario oficial](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm). Los comunicados salen a las **14:00, hora de Nueva York**.

### Antes del anuncio: ensayo de escenarios

Todavía no hay texto, pero puedes medir tu exposición ensayando un tono duro y uno suave:

```powershell
python -m macro_shock analyze --title "FOMC ensayo duro" --institution "Federal Reserve" --event-time "<fecha>T14:00:00-05:00" --data fred --text "Persistent inflation is a concern. Inflation too high. Further rate hikes may be appropriate." --portfolio portfolio.csv
python -m macro_shock analyze --title "FOMC ensayo suave" --institution "Federal Reserve" --event-time "<fecha>T14:00:00-05:00" --data fred --text "Inflation declining. Inflation approaching target. Rate cuts may be appropriate." --portfolio portfolio.csv
```

Usa `-05:00` en invierno y `-04:00` en verano. Compara el **"Worst 5% case"** de tu cartera en ambos ensayos. Si alguno supera lo que estás dispuesto a perder **por un solo evento**, reduce riesgo antes del anuncio, no después.

### Tras el anuncio

Copia el texto del comunicado (en inglés) de la web de la Fed y ejecuta:

```powershell
python -m macro_shock analyze --title "FOMC" --institution "Federal Reserve" --event-time "<fecha>T14:00:00-05:00" --data fred --text "<TEXTO>" --portfolio portfolio.csv --sectors --inflation
```

`--inflation` añade el **contraste con la inflación**: si el tono del comunicado coincide con lo que dice la energía (`CONSISTENT`), la lectura es más creíble; si no coincide (`CONTRADICTS`), desconfía de ella y vigila el bono a 2 años.

| Puntaje | Acción del motor | Interpretación práctica |
|---|---|---|
| < 25 | `NO_ACTION` | Nada que hacer |
| 25–45 | `MONITOR` | Atento a los próximos días con `daily` |
| 45–65 | `REDUCE` | Revisa si tu cartera está dentro de tu tolerancia |
| 65–80 | `HEDGE` | Considera cubrir o reducir las posiciones que más pierden en "Largest losses" |
| ≥ 80 | `EMERGENCY_DERISKING` | Riesgo muy alto: revisa la cartera completa |

⚠️ El análisis del texto busca **frases exactas**. Si sale `neutral` con un texto que claramente no lo es, apóyate en la sección `Market` (datos reales) y en tu propia lectura.

### Al día siguiente

Ejecuta `daily` y compara lo que pasó (VIX, crédito, S&P 500) con lo que estimó el motor. Con el tiempo aprenderás cuándo acierta y cuándo no.

---

## 2.1 Antes del PMI flash (≈ día 20–23 del mes)

Las encuestas de la Fed de Nueva York (≈ día 15) y de Filadelfia (≈ tercer jueves) salen antes del PMI flash de S&P Global.

```powershell
python -m macro_shock macro --quick
```

Lee `PMI hint` (dirección de la actividad manufacturera) y `PMI input-price hint` (precios pagados). Si ambas encuestas empeoran a la vez y los precios pagados suben, el riesgo es el escenario `supply_shock`: actividad a la baja con costes al alza.

## 2.2 Antes de cada IPC (≈ día 10–15 del mes)

Fechas del IPC: [calendario del BLS](https://www.bls.gov/schedule/news_release/cpi.htm). Se publica a las **8:30, hora de Nueva York**.

```powershell
python -m macro_shock nowcast
```

1. **Mira el IPC general estimado y su rango.** Si el dato publicado sale fuera del rango, hay una sorpresa real (casi siempre de la subyacente, que el motor no estima).
2. **Mira `to headline`**: qué subpartida aporta más. En un shock de energía, la gasolina domina; si es la electricidad o el gas, el efecto dura más, porque esas tarifas se ajustan con meses de retraso.
3. **Compara con el consenso** de cualquier calendario económico. Una estimación por encima del consenso apunta a un dato duro: más presión sobre los bonos y sobre los activos de larga duración (Nasdaq, bitcoin).
4. **El día del dato:** compara el dato publicado con la estimación. Con el tiempo sabrás cuánto acierta en tiempo real.

## 3. Cada semana (viernes)

```powershell
python -m macro_shock daily
```

La versión completa añade **la evidencia sectorial del régimen actual**, **la estimación del próximo IPC** y **el cuadrante macro**. Además:

```powershell
python -m macro_shock macro
```

1. **¿El mercado y los datos coinciden?** (`Markets vs data`). Si coinciden, la lectura es sólida. Si no, el mercado se está anticipando o exagerando: vigila si los datos le siguen las próximas semanas.
2. **Lee los sectores `**` y `*` del cuadrante actual** (marcado con `<- current`) y compáralos con los del régimen de riesgo de `daily`. **Cuando el régimen y el cuadrante apoyan la misma inclinación, la evidencia es más fuerte.**
3. **Mira la descomposición del 10 años**: si sube por la tasa real, presiona más a los activos de larga duración (Nasdaq, bitcoin) que si sube por el breakeven.

Además:

- Abre `reports\daily_log.csv` en Excel y crea un gráfico de `vix`, `hy_bps` y `vulnerability`, y otro de `wti`, `diesel_retail` y `crack_321` (margen de refino). **Las tendencias de varias semanas dicen más que un solo día.**
- Si la `vulnerability` sube de forma sostenida hacia 0.65, el mercado se acerca a `risk_off_correction`.

## 4. Cada mes

```powershell
python -m macro_shock optimize --max-drawdown 0.25
```

Sustituye 0.25 por tu límite.

1. Compara los pesos propuestos con tu cartera actual.
2. **Rebalancea solo si una clase (acciones, bonos o bitcoin) se ha desviado más de 5 puntos** de su peso objetivo. Rebalancear por diferencias pequeñas solo genera costes.
3. Si `Longer history check` avisa ⚠️, recuerda que en una crisis tipo 2008 la caída habría sido mayor que tu límite. Decide si lo aceptas.
4. Haz la prueba de estrés de la cartera resultante con un ensayo de evento (sección 2).

## 5. Cada trimestre

- **Actualiza el código** y comprueba que todo funciona:
  ```powershell
  cd $HOME\Macro-Shocks-Portfolio-Risk-Engine
  git pull
  cd Macro-Shocks-Portfolio-Risk-Engine
  python -m pytest python/tests/unit -q
  ```
- **Cambia tu clave de FRED** en [tu cuenta](https://fredaccount.stlouisfed.org/apikeys) y actualiza `.env`.
- **Revisa tu tolerancia a caídas**: ¿sigue siendo la misma?
- **Una vez al año**: añade las nuevas reuniones de la Fed al estudio FOMC (ver GUIA_USO.md, sección 5.6) y repite `fomc-study`.

---

## 6. Automatizar el informe diario

El script `scripts\run_daily.ps1` ejecuta `daily` y guarda la salida en `reports\daily_task.log`. Para programarlo de lunes a viernes a las 8:30, ejecuta en PowerShell (en una línea):

```powershell
schtasks /Create /TN "MSRE Daily" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:30 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File $HOME\Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine\scripts\run_daily.ps1"
```

- **Probarlo ahora:** `schtasks /Run /TN "MSRE Daily"`, y luego revisa la carpeta `reports`.
- **Eliminarlo:** `schtasks /Delete /TN "MSRE Daily" /F`
- La tarea solo se ejecuta si tu sesión de Windows está iniciada.
- Cada mañana tendrás el informe en `reports\daily_AAAA-MM-DD.txt`.

## 7. Reglas de oro

1. **"Sin alertas" significa no hacer nada.** La mayoría de los días lo correcto es no operar.
2. **Actúa solo con evidencia `**` o `*`.** Ignora `!` y lo que no tenga marca.
3. **Nunca reacciones a un solo día.** Espera a que un cambio de régimen se confirme.
4. **Respeta tu límite de caída.** Lo decidiste con calma; no lo cambies en medio de una caída.
5. **Los escenarios del motor son ilustrativos y la historia no es un pronóstico.** El motor da datos para decidir mejor, no decisiones.
