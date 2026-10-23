# Hoja de ruta operativa

Qué hacer con el motor, con qué frecuencia y cómo interpretar cada resultado. Para la instalación y el detalle de cada comando, consulta [GUIA_USO.md](GUIA_USO.md).

Todos los comandos se ejecutan desde la carpeta del proyecto:

```powershell
cd $HOME\Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine
```

## Resumen

| Cuándo | Comando | Tiempo | Para qué |
|---|---|---|---|
| **Cada día de mercado** | `daily --quick` | 1 min | Régimen y alertas |
| **Días de evento** (Fed, BCE…) | `analyze ... --portfolio portfolio.csv --sectors` | 5 min | Riesgo del anuncio para tu cartera |
| **Cada semana** (viernes) | `daily` (completo) | 2 min | Añade la evidencia sectorial del régimen |
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
2. **`Regime`**: el contexto.
3. **Los datos**: 10Y, curva, crédito, VIX, S&P 500 y bitcoin, con el valor de la ejecución anterior.

### Paso 3: decidir

| Lo que ves | Qué significa | Qué hacer |
|---|---|---|
| `ALERTS: none` | Nada relevante cambió | **Nada.** Es el resultado más frecuente, y lo correcto es no operar |
| `REGIME CHANGE` | El mercado pasó a otro estado de vulnerabilidad | Sigue la lista de pasos del informe (sección 1.1) |
| `VIX up` o `HY spread up`, sin cambio de régimen | La tensión sube, pero aún no cambia el régimen | Vigila. Si hay un evento cerca, haz la prueba de estrés (sección 2) |
| `below its 52-week high` | El S&P 500 está en corrección (−10% o peor) | Comprueba que tu cartera sigue dentro de tu límite de caída. **No vendas en pánico**: tras la tensión, el mes siguiente suele traer rebote (ver 1.2) |

### 1.1 Si cambia el régimen

1. Ejecuta `python -m macro_shock sector-study` y lee los resultados `**` y `*` del nuevo régimen.
2. Ejecuta `python -m macro_shock optimize --max-drawdown <tu límite>`: la inclinación por régimen puede cambiar los pesos.
3. Haz la prueba de estrés de tu cartera: `analyze ... --data fred --portfolio portfolio.csv`.
4. **Cambia posiciones solo si la evidencia es `**` o `*`**, nunca por el movimiento de un solo día. Un cambio de régimen que se revierte al día siguiente no es una señal.

### 1.2 Qué significa cada régimen

Con los datos de FRED, el motor distingue cuatro regímenes. El de **crisis** es posible gracias a los índices de estrés financiero de la Fed (`STLFSI4` y `NFCI`), que aportan los componentes de liquidez y financiación.

| Régimen | Mercado | Evidencia histórica desde 1990 (ejecución de octubre de 2026, antes de los índices de estrés) |
|---|---|---|
| `risk_on_expansion` | Tranquilo: VIX bajo, crédito barato | Momentum `*` favorecido; pequeñas empresas y consumo duradero `*` rezagados |
| `fragile_risk_on` | Calma aparente, con algo de tensión | **Momentum `**` favorecido** (el único resultado robusto); telecom `*` rezagado |
| `risk_off_correction` | Tensión: VIX alto y/o crédito caro | Tecnología `*` favorecida y eléctricas `*` rezagadas el mes siguiente: **efecto rebote**. El momentum pierde su ventaja |
| `crisis` | Pánico: VIX extremo, crédito muy caro y estrés financiero en máximos históricos | Ver `sector-study`: hay pocos periodos (2008, 2020), así que la evidencia será escasa |

Esta tabla es una foto de octubre de 2026. **La referencia actualizada es siempre la salida de `sector-study`**, que se recalcula con cada ejecución. Desde que el motor usa los índices de estrés, los regímenes históricos se reclasifican y los resultados pueden cambiar.

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
python -m macro_shock analyze --title "FOMC" --institution "Federal Reserve" --event-time "<fecha>T14:00:00-05:00" --data fred --text "<TEXTO>" --portfolio portfolio.csv --sectors
```

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

## 3. Cada semana (viernes)

```powershell
python -m macro_shock daily
```

La versión completa añade **la evidencia sectorial del régimen actual**. Además:

- Abre `reports\daily_log.csv` en Excel y crea un gráfico de `vix`, `hy_bps` y `vulnerability`. **Las tendencias de varias semanas dicen más que un solo día.**
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
