# Guía de uso del Macro Shock Risk Engine

Esta guía explica cómo instalar, configurar y usar el motor desde Windows (PowerShell), y cómo interpretar lo que muestra cada comando. La rutina de trabajo diaria, semanal y mensual está en [RUTINA_DIARIA.md](RUTINA_DIARIA.md).

> **Qué es y qué no es.** El motor es una herramienta de **análisis de riesgo e investigación**. Mide el estado del mercado, simula cómo podría reaccionar ante un evento de política monetaria y muestra lo que los datos históricos respaldan. **No es un sistema de recomendaciones de inversión**: sus escenarios son plantillas ilustrativas, y sus estadísticas, historia, no pronósticos.

---

## 1. Requisitos

| Necesitas | Para qué |
|---|---|
| **Python 3.11 o superior** | Ejecutar el motor |
| **Git** | Descargar y actualizar el código |
| **Clave gratuita de FRED** | Datos reales de mercado ([solicítala aquí](https://fred.stlouisfed.org/docs/api/api_key.html)) |
| **Conexión a internet** | FRED (Reserva Federal de St. Louis) y la biblioteca de Kenneth French (Dartmouth) |

**No necesitas Claude ni ninguna IA para usarlo.** Todo se ejecuta en tu computadora.

## 2. Instalación (una sola vez)

**En un PC nuevo**, lo más sencillo es el instalador automático: consulta [INSTALACION_NUEVO_PC.md](INSTALACION_NUEVO_PC.md). Instalación manual:

En PowerShell, **un comando por línea**:

```powershell
cd $HOME
git clone -c core.autocrlf=false https://github.com/yohancristobalr-code/Macro-Shocks-Portfolio-Risk-Engine.git
cd Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine
pip install -e .
pip install fredapi pytest
```

## 3. Configurar la clave de FRED

Crea el archivo `.env` en la carpeta interior, `...\Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine`, en una línea sola:

```powershell
Set-Content -Path .env -Value "FRED_API_KEY=TU_CLAVE" -Encoding ascii
```

- `.env` está en `.gitignore`: **nunca se sube a GitHub**. Compruébalo con `git status`: no debe aparecer.
- **No pegues nunca la clave en un chat**, un correo o un documento.
- Los comandos encuentran `.env` tanto desde la carpeta interior como desde la exterior.

## 4. Actualizar el motor

```powershell
cd $HOME\Macro-Shocks-Portfolio-Risk-Engine
git checkout main
git pull
```

Para comprobar que todo funciona tras una actualización, desde la carpeta interior:

```powershell
python -m pytest python/tests/unit -q
```

Debe terminar con `passed` y sin `failed`.

---

## 5. Los comandos

Todos se ejecutan con `python -m macro_shock <comando>`. Añade `--help` a cualquiera para ver sus opciones.

| Comando | Qué responde | Frecuencia |
|---|---|---|
| `daily` | ¿Cómo está el mercado hoy? ¿Cambió algo? | Diaria |
| `analyze` | ¿Cuánto riesgo implica este anuncio de un banco central? ¿Cómo afectaría a mi cartera? | Días de evento |
| `sector-study` | ¿Qué sectores y estilos funcionaron históricamente en cada régimen? | Semanal |
| `optimize` | ¿Qué pesos dan el máximo de acciones dentro de mi tolerancia a caídas? | Mensual |
| `fomc-study` | ¿Cómo reaccionaron bitcoin, la bolsa y el VIX a la Fed? | Ocasional |
| `nowcast` | ¿Cuánto subirá el próximo IPC por la energía? | Antes de cada IPC |
| `report` | **Todo lo anterior en un informe HTML ejecutivo**, con gráficas, análogos históricos y modelo predictivo | Antes de operar |
| `crypto` | ¿Cómo están las 10 criptos, su apalancamiento, el sentimiento y la liquidez? ¿Qué funcionó en este régimen? | Diaria o semanal |
| `macro` | ¿Qué escenario macro descuenta el mercado? ¿Qué dicen los datos de crecimiento e inflación? ¿Qué sectores funcionan en ese cuadrante? | Semanal |

### 5.1 `daily` — informe diario

```powershell
python -m macro_shock daily
python -m macro_shock daily --quick     # sin el estudio sectorial (unos segundos)
```

Muestra los datos del día, el **régimen**, las **alertas** frente a la ejecución anterior y la evidencia sectorial para el régimen. También muestra una línea de **energía** (WTI, Brent, gas natural Henry Hub, gasolina y diésel en surtidor, margen de refino), una línea **`Macro`** con el escenario que descuenta el mercado (sección 5.8) y, en la versión completa, la **estimación del próximo IPC** (sección 5.7) y el **cuadrante macro**. Guarda:

- `reports\daily_AAAA-MM-DD.txt`: el informe de ese día.
- `reports\daily_log.csv`: una fila por día. Ábrelo con Excel para ver la evolución.

Ambos están en `.gitignore`.

**Alertas:**

| Alerta | Significado |
|---|---|
| `REGIME CHANGE` | El régimen cambió desde la última ejecución. Sigue la lista de pasos que aparece debajo. |
| `VIX up` | El VIX subió 5 puntos o más. La tensión aumenta rápidamente. |
| `HY spread up` | El crédito de alto rendimiento se encareció 50 pb o más. Hay estrés en el crédito. |
| `below its 52-week high` | El S&P 500 está un 10% o más por debajo de su máximo: es una corrección. |
| `WTI crude up` / `Brent crude up` | El crudo subió un 15% o más en 30 días: presión sobre el IPC energético. Con `down`, alivio. |
| `Gasoline, retail up` / `Diesel, retail up` | El surtidor subió un 10% o más en 30 días. Con `down`, alivio. |
| `MACRO QUADRANT CHANGE` | Los datos de crecimiento e inflación pasaron a otro cuadrante (solo en la versión completa). Ejecuta `macro` para ver los sectores del nuevo cuadrante. |

### 5.2 `analyze` — analizar un evento

```powershell
python -m macro_shock analyze --title "FOMC" --institution "Federal Reserve" --event-time "2026-12-09T14:00:00-05:00" --data fred --text "TEXTO DEL COMUNICADO EN INGLÉS" --portfolio portfolio.csv --sectors
```

| Opción | Efecto |
|---|---|
| `--data fred` | Usa el mercado real. Sin esta opción, usa un mercado sintético. |
| `--text` | El texto del comunicado, **en inglés**. |
| `--portfolio portfolio.csv` | Añade la prueba de estrés de tu cartera. |
| `--sectors` | Añade la evidencia sectorial para el régimen detectado. |
| `--inflation` | Compara el tono del comunicado con la estimación del IPC a partir de la energía. |

**Cómo leerlo:**

- **`Policy`**: tono del texto (`hawkish` = más duro, `dovish` = más suave). ⚠️ El análisis busca **frases exactas** como "inflation too high"; con textos reales, a menudo saldrá `neutral` aunque el tono no lo sea.
- **`COMPOSITE RISK SCORE`** (0–100) y nivel de acción:

  | Puntaje | Gravedad | Acción sugerida |
  |---|---|---|
  | < 25 | LOW / INFO | `NO_ACTION` |
  | 25–45 | LOW / MEDIUM | `MONITOR` |
  | 45–65 | MEDIUM / HIGH | `REDUCE` |
  | 65–80 | HIGH / CRITICAL | `HEDGE` |
  | ≥ 80 | CRITICAL | `EMERGENCY_DERISKING` |

- **`Reliability: LOW`** es normal con FRED: cubre 5 de los 8 tipos de datos de mercado (no tiene amplitud de mercado, divisas ni materias primas).
- **`YOUR PORTFOLIO`**: ganancia o pérdida esperada, la del peor 5% de los casos, y qué posiciones pierden más en el peor escenario.
- **`INFLATION CROSS-CHECK`** (con `--inflation`): `CONSISTENT` si un tono duro coincide con una inflación estimada por encima del 2% anualizado (o un tono suave, por debajo), lo que hace la lectura más creíble. `CONTRADICTS` si no coinciden: desconfía de la lectura y vigila el bono a 2 años. Si la inflación estimada está cerca del 2% (entre 1% y 3% anualizado) o el tono es `neutral`, no confirma nada.
- **`Bitcoin`**: estimado como **1.0 × el movimiento de la bolsa** (1.16 × en los escenarios extremos). Son las betas medidas con datos de FRED de 2021–2025 (1.04 y 1.16).

### 5.3 Tu cartera: `portfolio.csv`

Copia el ejemplo y edítalo con tus posiciones:

```powershell
Copy-Item examples\portfolio_example.csv portfolio.csv
```

Formato: `name,type,value,sensitivity`

| `type` | Para | `sensitivity` (por defecto) |
|---|---|---|
| `equity` | acciones, ETF de bolsa | beta frente al S&P 500 (1.0) |
| `bond` | bonos largos | duración en años (7) |
| `short_bond` | bonos cortos | duración en años (2) |
| `high_yield` | bonos high yield | duración del spread (4) |
| `gold`, `oil`, `usd` | oro, petróleo, dólar | beta (1.0) |
| `bitcoin` | bitcoin y otras cripto | beta frente a bitcoin (1.0; Ethereum ≈ 1.3) |
| `cash` | efectivo | — |

`portfolio.csv` está en `.gitignore`: **nunca se sube a GitHub**.

### 5.4 `sector-study` — sectores y estilos por régimen

```powershell
python -m macro_shock sector-study
python -m macro_shock sector-study --start 2008-01-01     # solo desde 2008
```

Para cada régimen desde 1990, muestra qué sectores superaron al mercado **el mes siguiente** y cómo funcionaron los estilos (pequeñas frente a grandes empresas, *value* frente a *growth*, momentum).

**Las marcas son lo más importante:**

| Marca | Significado | Qué hacer |
|---|---|---|
| `**` robusto | Claro en el total y en **cada mitad** del historial | Es la evidencia más fiable |
| `*` consistente | Claro en el total, mismo signo en ambas mitades | Pista débil; desconfía si una mitad está cerca de 0 |
| `!` inestable | Cambió de signo entre mitades | **Ignorar** |
| (nada) | Sin evidencia clara | Ignorar |

- **`Credit proxy check`**: el crédito histórico se reconstruye con Moody's BAA (FRED solo publica 3 años de las series ICE que usa el motor). El porcentaje indica cuánto coincide con el crédito real. Los desacuerdos suelen darse entre los dos regímenes tranquilos.
- **Hallazgos de la ejecución de octubre de 2026** (antes de añadir los índices de estrés): el único robusto fue el **momentum en `fragile_risk_on`**. Tras la tensión (`risk_off_correction`), la tecnología tendió a superar al mercado y las eléctricas a quedarse atrás: es el efecto rebote. Con los índices de estrés, los regímenes se clasifican mejor y estos resultados se recalculan: consulta siempre la salida actual.
- **Índices de estrés financiero:** el régimen usa también el índice de estrés financiero de la Fed de St. Louis (`STLFSI4`, desde 1993) y el de condiciones financieras de la Fed de Chicago (`NFCI`, desde 1971), medidos como percentil de su propia historia. Gracias a ellos el motor puede detectar el régimen de **crisis**.

### 5.5 `optimize` — pesos de cartera

```powershell
python -m macro_shock optimize --max-drawdown 0.25
python -m macro_shock optimize --stocks 0.5                 # porcentaje fijo en acciones
python -m macro_shock optimize --max-drawdown 0.25 --no-bitcoin
```

- **Universo:** 11 sectores de EE. UU. (con su ETF), bonos del Tesoro a 10 años (IEF) y 2 años (SHY), y bitcoin (3% por defecto, `--btc-weight`).
- **`--max-drawdown 0.25`**: elige **la combinación con más acciones** cuya peor caída **no supera el −25%**, tanto en los últimos 10 años como **desde 1990** (incluidas 2000-02 y 2008). La tabla muestra todas las opciones, para que veas qué ganas y qué arriesgas.
- **Inclinación por régimen** (`--tilt`, 25% por defecto): ajusta los sectores con evidencia `*` o `**` en el régimen actual. Con `--tilt 0` se desactiva.
- **Guarda `portfolio_optimized.csv`**, listo para usar con `analyze --portfolio`.

**Cómo leerlo:**

- **`Risk share`**: cuánto del riesgo aporta cada activo. Si sale ≈0% o negativo en los bonos, significa que **amortiguan** el riesgo de las acciones.
- **`Longer history check`**: si avisa ⚠️, tu límite se habría superado en una crisis antigua. Decide si lo aceptas o bajas el límite.
- **El bloque de acciones no replica el S&P 500**: reparte el riesgo por igual entre sectores, así que la tecnología pesa ~5%, no ~30% como en el índice.
- **Rentabilidades:** las de 2016–2026 corresponden a una década excepcional; no esperes que se repitan.

### 5.6 `fomc-study` — bitcoin frente a la Fed

```powershell
python -m macro_shock fomc-study --start 2021-01-01 --end 2025-12-31 --output fomc_events.csv
```

Mide cómo se movieron bitcoin, el S&P 500 y el VIX en los anuncios de la Fed de 2017–2025, según la sorpresa (movimiento del bono a 2 años ese día). **Principales conclusiones:** bitcoin apenas reacciona a la sorpresa, pero desde 2021 se mueve con la bolsa (beta ≈ 1).

⚠️ **Las fechas de la Fed llegan hasta diciembre de 2025.** Para añadir reuniones nuevas, edita `FOMC_ANNOUNCEMENTS` en `python/src/macro_shock/research/fomc_study.py`, con las fechas de [la web de la Fed](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm). La línea `Date check` confirmará que coinciden con los cambios de tipos en FRED.

### 5.7 `nowcast` — el próximo IPC a partir de la energía

```powershell
python -m macro_shock nowcast
python -m macro_shock nowcast --output nowcast_backtest.csv    # guarda el backtest mes a mes para Excel
```

Estima el **IPC de EE. UU. del mes que todavía no se ha publicado** a partir de los precios diarios y semanales de la energía en FRED, subpartida por subpartida:

| Subpartida del IPC | Qué la mueve |
|---|---|
| Gasolina | Precio de la gasolina en surtidor (semanal) |
| Fuel oil | Precio del diésel en surtidor, este mes y el anterior |
| Electricidad | Gas natural Henry Hub, con 1–3 meses de retraso |
| Gas por tubería | Gas natural Henry Hub, este mes y los 2 anteriores |

1. Cada subpartida se estima con una regresión sobre la historia desde los años 90, con efectos por mes del año, porque el IPC está desestacionalizado y los precios no.
2. **Energía** = las subpartidas ponderadas por su peso real dentro del IPC energético, y **IPC general** = subyacente + alimentos + energía con sus pesos. Los pesos se miden con los últimos 5 años del IPC publicado, sin introducirlos a mano.
3. La subyacente y los alimentos no tienen precios diarios: entran con su **media de los últimos 3 meses**.

**Cómo leerlo:**

- **`m/m`**: variación mensual estimada. **`90% range`**: el rango donde cayó el 90% de los errores del backtest.
- **`share`**: peso de la subpartida dentro de la energía, o de cada bloque dentro del IPC general.
- **`to headline`**: cuántos puntos aporta cada subpartida al IPC general. Es la forma de ver **qué está empujando la inflación**.
- **`y/y`**: variación interanual. Sale `n/a` si FRED no tiene el dato de hace un año: es el caso de octubre de 2026, porque el IPC de octubre de 2025 no se publicó por el cierre del gobierno de EE. UU.
- **`(81% of the month observed)`**: qué parte del mes tiene datos. El resto se rellena con el último precio, así que a principios de mes la estimación es más incierta que el rango.
- **`Energy prices`**: último precio, media del mes frente a la del mes anterior y variación en un año. El **margen de refino 3-2-1** es lo que gana una refinería por convertir 3 barriles de crudo en 2 de gasolina y 1 de diésel: si sube, el surtidor sube más que el crudo.
- **`Backtest since 2010`**: el error medio de cada estimación, comparado con una estimación ingenua (la media histórica; para el IPC general, la media de los últimos 3 meses). Si alguna línea dice `NO`, **no te fíes de esa subpartida**.

⚠️ **Limitaciones:**
- Es un nowcast **energético**: la subyacente se supone igual a su tendencia, así que una sorpresa en la subyacente no la capta.
- El backtest usa meses completos de datos y la versión actual (revisada) del IPC; en tiempo real, el error es algo mayor.
- **El PMI no se puede estimar así**: es una encuesta de S&P Global cuyos datos no son públicos. Su componente de precios de insumos suele moverse en la misma dirección que la energía, así que este nowcast sirve como pista, no como estimación del PMI.

### 5.8 `macro` — escenario de mercado, crecimiento, inflación y cuadrante

```powershell
python -m macro_shock macro
python -m macro_shock macro --quick       # sin el estudio de sectores por cuadrante
python -m macro_shock macro --window 10   # movimientos de los últimos 10 días en lugar de 20
```

Responde las preguntas de un informe de riesgo multiactivo con datos de FRED, en cinco bloques:

**1. Qué descuenta el mercado** (últimos 20 días hábiles). Mide el movimiento de 11 variables en **desviaciones típicas (`sd`)** respecto a su movimiento normal de los últimos 3 años y lo compara con los cuatro escenarios:

| Escenario | Patrón que lo confirma |
|---|---|
| `reacceleration`: reaceleración con presión de costos | Suben el 2 años, la tasa real, el breakeven, el petróleo y la bolsa; el crédito se abarata |
| `disinflation`: desinflación / aterrizaje ordenado | Bajan el 10 años, el breakeven y el petróleo; sube la bolsa; bajan el crédito y el VIX |
| `supply_shock`: shock de oferta / inflación persistente | Suben el petróleo y el breakeven; la curva se empina; baja el Nasdaq; se encarece el crédito |
| `growth_scare`: freno de crecimiento / liquidación | Bajan el 10 años y la tasa real; baja la bolsa; suben el crédito, el VIX y el dólar |

- **`score`** (−1 a +1): cuánto encajan los movimientos con el patrón. `CONFIRMED` desde 0.35, `partial` desde 0.15, `invalidated` por debajo de −0.15.
- **`confirms` / `contradicts`**: qué movimientos apoyan el escenario y cuáles lo contradicen.
- **La descomposición del 10 años**: si sube por la **tasa real** (crecimiento o una Fed dura) o por el **breakeven** (miedo a la inflación).

**2. Crecimiento: indicadores adelantados.** Peticiones de subsidio por desempleo, el índice económico semanal de la Fed de Nueva York (en % de crecimiento del PIB), GDPNow de la Fed de Atlanta, la **regla de Sahm** (≥ 0.5 = señal de recesión) y las **encuestas manufactureras de Filadelfia y Nueva York**. Estas encuestas salen antes del PMI flash: `PMI hint` indica si la actividad mejora o empeora, y `PMI input-price hint` si los precios pagados suben.

**3. Inflación:** la línea del nowcast del IPC (sección 5.7).

**4. Cuadrante macro**, solo con datos publicados:

| | Inflación sube | Inflación baja |
|---|---|---|
| **Crecimiento sube** | `reacceleration` | `disinflation` |
| **Crecimiento baja** | `supply_shock` (estanflación) | `growth_scare` (desaceleración) |

- El **eje de crecimiento** combina peticiones de subsidio, producción industrial y empleo; el **eje de inflación**, el IPC general y el subyacente. Cada señal compara el ritmo de los últimos 3 meses con el del último año, en unidades de su tamaño típico.
- **`with the October CPI nowcast`**: cómo quedaría el eje de inflación si el próximo IPC sale como estima el nowcast.
- **`Markets vs data`**: `AGREE` si el mercado y los datos señalan el mismo escenario, la lectura más fuerte. `DISAGREE` si no: el mercado se anticipa, así que o los datos están a punto de girar o el mercado exagera.

**5. Sectores y estilos por cuadrante desde 1972.** Igual que `sector-study`, pero con el cuadrante macro en lugar del régimen de riesgo, y solo los resultados `**` y `*`. La historia reconstruye el cuadrante con lo que se había publicado en cada fecha (con los retrasos de publicación de cada dato), así que no usa información del futuro.

⚠️ **Limitaciones:**
- El bloque 1 describe **lo que el mercado ya descuenta**, no lo que va a pasar.
- **El oro y la plata no están**: FRED dejó de publicar sus precios en 2022.
- El PMI de S&P Global y el ISM no son públicos: las encuestas regionales solo indican su dirección.

### 5.9 `report` — informe ejecutivo en HTML

```powershell
python -m macro_shock report --open      # genera reports\informe_AAAA-MM-DD.html y lo abre
python -m macro_shock report --quick     # sin sectores, análogos ni modelo (más rápido)
```

Reúne todos los estudios en un solo archivo que se abre sin internet, con tema claro u oscuro según tu sistema. Después de `install.ps1`, el acceso directo **"Informe Macro"** del escritorio hace lo mismo con un doble clic.

- **Lectura ejecutiva**: frases generadas a partir de los números, sin opiniones: régimen, escenario de mercado, tasas, cuadrante, inflación, crecimiento, energía, análogos, modelo y convergencia sectorial.
- **Gráficas de tendencia**: tasas (nominal, real y breakeven), puntaje de los escenarios en el último año, ejes macro en 15 años, IPC interanual, peticiones de subsidio, crudo, surtidor, margen de refino, gas, VIX, crédito y bolsa.
- **Análogos históricos**: los 5 meses desde 1990 más parecidos a hoy (VIX, curva, spread Baa, cambio del 10 años y del WTI en 12 meses, ejes de crecimiento e inflación), separados al menos un año entre sí, con lo que hicieron después la bolsa (3, 6 y 12 meses y caída máxima), el 10 años, el petróleo y los sectores, frente a la media de todos los meses.
- **Modelo predictivo**: regresión *ridge* del exceso de rentabilidad de la bolsa a 3 meses sobre esas mismas variables, **reestimada cada mes desde 2005 solo con datos anteriores**. Si el **R² fuera de muestra** es ≤ 0, el informe lo dice y la previsión **no debe usarse**: la media histórica predice igual o mejor.
- Si una fuente de datos falla, el informe se genera igual e indica qué sección falta y por qué.

### 5.10 `crypto` — eje cripto y liquidez de la Fed

```powershell
python -m macro_shock crypto
```

No necesita ninguna clave ni cuenta: usa APIs públicas (si tienes la clave de FRED, añade el Nasdaq, el cuadrante y la liquidez de la Fed). En el informe HTML es la sección «Cripto».

**Las 10 monedas:** BTC, ETH, SOL, AAVE y ONDO (tu elección), más XRP, BNB, DOGE, ADA y LINK: las mayores no estables con más de 5 años de historia en Binance, que cubren pagos, exchange, plataformas, oráculos y memes. Las stablecoins (USDT, USDC) no entran en el análisis de precio, pero su oferta total se usa como indicador de liquidez. La lista está en `COINS`, en `python/src/macro_shock/research/crypto.py`.

| Dato | Fuente | Cómo leerlo |
|---|---|---|
| Precios desde 2017 | Binance (espejo público `data-api.binance.vision`) | Rendimiento a 7, 30, 90 días y 1 año; volatilidad; caída desde el máximo de 12 meses |
| Beta y correlación | Cálculo propio (90 días) | Beta > 1: amplifica los movimientos de BTC. Correlación con el Nasdaq ≥ 0.6: cotiza como activo de riesgo |
| **Funding** | Futuros de Binance | Lo que pagan los largos apalancados. **≥ 30% anual: exceso de apalancamiento** (riesgo de liquidaciones en cascada). Negativo: dominan los cortos |
| Interés abierto | Futuros de Binance (30 días) | Si sube mucho junto al precio, la subida va apalancada |
| Capitalización, ranking, dominancia de BTC | CoinGecko | Dominancia al alza: el dinero se refugia en BTC |
| Fear & Greed | alternative.me | ≥ 75 codicia extrema, ≤ 25 miedo extremo |
| Oferta de stablecoins | DefiLlama | Si cae, sale liquidez del ecosistema |

**Régimen cripto** (a partir de BTC): tendencia (por encima o por debajo de su media de 200 días) × volatilidad (la de 30 días frente a su mediana anual). La evidencia muestra qué monedas superaron a BTC en los 30 días siguientes en cada régimen, y cómo rindieron BTC y ETH en cada **cuadrante macro**, con las mismas marcas `**`/`*`. Las monedas jóvenes (ONDO desde 2024) tienen pocas ventanas y casi nunca alcanzan una marca: es lo correcto, no un fallo.

**Liquidez de la Fed** (sección «Liquidez de la Fed» del informe): balance de la Fed − cuenta del Tesoro − repos inversos, en billones de dólares, y expectativas de tipos (bono a 2 años − fed funds: positivo, el mercado espera subidas; negativo, bajadas). El informe **mide** la correlación de la liquidez con el Nasdaq y BTC en los últimos 6 años, en lugar de suponerla.

⚠️ Si Binance está restringido en tu país, los futuros (funding e interés abierto) pueden fallar: el resto sigue funcionando y la sección «Salud de los datos» lo indica.

### 5.10b Página 1: visión sistemática por activo

Arriba del informe, una tabla como la que abre los informes de los bancos: **sobreponderar, neutral o infraponderar** para bolsa, bonos, crédito, oro, cobre, petróleo, dólar y bitcoin. Cada activo suma señales de +1 (a favor) o −1 (en contra):

| Señal | Regla |
|---|---|
| Tendencia | Precio por encima de su media de 200 días |
| Momentum | Rentabilidad de 3 meses positiva |
| Escenario | Dirección del activo en el escenario que descuenta el mercado, según el manual estándar (×1 si está confirmado, ×0.5 si es parcial) |
| Cuadrante | Dirección del activo en el cuadrante de los datos publicados |

≥ +2: sobreponderar; ≤ −2: infraponderar. Para bonos y crédito, "a favor" significa que bajan su rentabilidad o su spread. Debajo aparecen los **3 sectores preferidos y los 3 a evitar**. Son reglas fijas y transparentes, no una recomendación.

### 5.10c Sectores del S&P 500 y metales

**Sectores:** los 11 ETF SPDR frente al SPY, con precios diarios de **Stooq** (gratis, sin clave):
- Rentabilidad relativa a 1, 3, 6 y 12 meses; tendencia; ranking de momentum.
- **Sensibilidades medidas** de cada sector al bono a 10 años, el petróleo, el dólar y el crédito, en periodos de 20 días durante 5 años.
- **Mapa de escenarios**: cuánto rendiría cada sector frente al SPY si cada escenario se da con un movimiento típico de sus variables, y con los movimientos reales del último mes.
- **Amplitud** (cuántos sectores están en tendencia alcista) y **rotación** (cíclicos frente a defensivos).

**Metales:** oro y plata de Stooq (respaldo: los perpetuos XAUUSDT y XAGUSDT de Binance, que además dan el funding) y cobre de Stooq (respaldo: el precio mensual del FMI en FRED).
- **Oro frente a la tasa real**: distancia del oro a su relación de 3 años con el TIPS a 10 años. Más de +1 sd, caro respecto a esa relación; menos de −1 sd, barato.
- **Ratio oro/plata** con su percentil de 10 años. Alto: miedo.
- **Cobre/oro**: el indicador de crecimiento que siguen los bancos, con su correlación con el bono a 10 años.

**Estructura del VIX** (sección «Régimen de riesgo»): VIX frente al VIX a 3 meses (`VXVCLS`). Por encima de 1, el mercado descuenta estrés inmediato. `daily` lo avisa.

### 5.11 Carry trade del yen

Muchos inversores se endeudan en yenes (tipos casi nulos) para invertir en activos que rinden más. Si el yen sube con fuerza, tienen que recomprarlo para devolver el préstamo y venden lo que compraron: en agosto de 2024 ese desmontaje hundió el Nasdaq y bitcoin en pocos días.

| Dato | Cómo leerlo |
|---|---|
| USDJPY y su cambio en 10 días | Si baja, el yen se fortalece |
| Volatilidad de 20 días (percentil de 3 años) | Por encima del percentil 90: el carry está bajo tensión |
| Diferencial a 3 meses EE. UU. − Japón | Lo que gana el carry; **carry / riesgo** alto = operación atractiva y abarrotada |
| **Señal de desmontaje** | El yen sube un 3% o más en 10 días hábiles. También aparece como alerta en `daily` |

El informe **mide** qué hicieron el Nasdaq y BTC en los 30 días siguientes a cada episodio desde 2000, frente a cualquier periodo de 30 días.

### 5.12 Historial de aciertos

Cada `report` guarda sus previsiones en `reports\predicciones.csv`: el nowcast del IPC con su rango, la mediana de los análogos a 6 meses y la previsión del modelo a 3 meses. Los informes siguientes evalúan las que ya tienen resultado: el error medio, el porcentaje de IPC dentro del rango del 90% y el porcentaje de signos acertados. **Tras unos meses sabrás, con datos propios, cuánto fiarte de cada pieza.** Si generas varios informes el mismo día, solo cuenta el último.

### 5.13 Valoración, posicionamiento y calendario

**Valoración de la bolsa** (solo FRED, sin datos de pago):

| Indicador | Cálculo | Cómo leerlo |
|---|---|---|
| PER aproximado | Valor de mercado de las acciones de EE. UU. (cuentas Z.1 de la Fed) / beneficios empresariales (BEA) | Su **percentil desde 1951**. El nivel no coincide con el PER del S&P 500 publicado; lo útil es la comparación histórica |
| Indicador Buffett | Valor de mercado / PIB | Igual: el percentil |
| Prima de riesgo | Rentabilidad de los beneficios − tasa real a 10 años | Baja: la bolsa paga poco frente a bonos sin riesgo (cara). Desde 2003 |

El dato trimestral se lleva a hoy con el S&P 500. El informe mide además **qué rentabilidad anual dio la bolsa los 5 años siguientes** cuando la valoración estaba en el mismo quinto de su historia que hoy. En la página 1, una prima de riesgo en su 20% más bajo resta un punto a la bolsa y en su 20% más alto lo suma.

**Posicionamiento (CFTC):** el informe semanal de compromisos de operadores (API pública, sin clave) para S&P 500, bono a 10 años, yen, petróleo, oro, cobre y Bitcoin. El **índice COT** sitúa la posición neta de los especuladores en su rango de 3 años: 100 es la más larga y 0 la más corta. **≥ 90 o ≤ 10 es un extremo**, que suele ser señal contraria: cuando todos están largos, quedan pocos compradores. En la página 1 resta un punto si el extremo es largo y lo suma si es corto.

**Calendario:** debajo de la página 1, las publicaciones clave de los próximos 14 días (IPC, empleo, PIB, PCE, PPI, ventas minoristas…, del calendario de FRED) y las reuniones de la Fed. El PMI de S&P Global no está en FRED: se publica hacia el día 20–23 de cada mes.

### 5.14 Salud de los datos

Revisa cada serie que usó el informe: último dato, días de antigüedad, frecuencia y estado (**al día**, **desactualizada** o **falló**). Los problemas aparecen arriba de la tabla. Si una serie falla varios días seguidos, probablemente la fuente cambió su identificador: es lo primero que hay que corregir.

---

## 6. Problemas frecuentes

| Mensaje | Causa | Solución |
|---|---|---|
| `FRED_API_KEY is not set` | No encuentra `.env` | Créalo en la carpeta interior (sección 3) |
| `syntax error: line 1, column 0` | FRED está caído o en mantenimiento | Vuelve a intentarlo más tarde |
| `Bad Request ... api_key is not registered` | Clave inválida | Revisa `.env` o pide una clave nueva |
| `No se encuentra ningún parámetro...` en PowerShell | Dos comandos en una línea | Un comando por línea |
| `Activate.ps1 ... ejecución de scripts deshabilitada` | Política de PowerShell | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `Could not download ... ken.french` | La web de Dartmouth no responde | Inténtalo más tarde; `daily --quick` funciona sin ella |

## 7. Cuando ya no tengas acceso a Claude

- **Seguirá funcionando igual**: no hay ninguna dependencia de IA.
- **Si algo deja de funcionar**, casi siempre será porque una fuente de datos cambió: una serie de FRED que deja de publicarse, como pasó con `TEDRATE` en 2022, o un cambio de formato en la biblioteca de French. Ejecuta los tests (sección 4) y el mensaje de error dirá qué serie o archivo falla.
- **Para modificarlo**, el código está organizado por módulos:

  | Qué cambiar | Dónde |
  |---|---|
  | Series de FRED | `python/src/macro_shock/data/feed_provider.py` |
  | Estudios (FOMC, sectores, regímenes, cartera, nowcast del IPC, escenario macro y cuadrante) | `python/src/macro_shock/research/` |
  | Escenarios y beta de bitcoin | `python/src/macro_shock/scenario_engine/scenario_tree.py` |
  | Comandos | `python/src/macro_shock/__main__.py` |

  Cada cambio debería ir acompañado de su test en `python/tests/unit/`.
