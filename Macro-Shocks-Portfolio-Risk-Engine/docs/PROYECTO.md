# Macro Shock Risk Engine: documento del proyecto

Presentación, estado actual, fórmulas a revisar, hoja de ruta y contexto para continuar el desarrollo en otra conversación o con otra persona.

Documentos relacionados:
- [GUIA_USO.md](GUIA_USO.md): el manual, con todos los comandos.
- [RUTINA_DIARIA.md](RUTINA_DIARIA.md): qué hacer cada día, semana y mes.
- [INSTALACION_NUEVO_PC.md](INSTALACION_NUEVO_PC.md): instalación en otro PC.
- [CAMBIOS.md](CAMBIOS.md): control de cambios.

---

## 1. Qué es

Un motor de análisis macro y de riesgo multiactivo que se ejecuta en un PC personal con **datos públicos y gratuitos**: FRED, Kenneth French Data Library, Binance, CoinGecko, alternative.me, DefiLlama, Stooq y CFTC. Responde cada mañana, en un informe HTML ejecutivo, a las preguntas de un comité de inversión:

1. ¿Qué escenario macro descuenta el mercado y qué lo invalidaría?
2. ¿Qué dicen los datos de crecimiento e inflación, y coinciden con el mercado?
3. ¿Cuánto subirá el próximo IPC y qué lo empuja?
4. ¿Cuál es el régimen de riesgo y qué sectores, estilos, metales y criptos funcionaron históricamente en él?
5. ¿Está cara la bolsa? ¿Están los especuladores en un extremo?
6. ¿Qué riesgos vigilar: liquidez de la Fed, carry del yen, apalancamiento cripto, estructura del VIX?
7. ¿Cuánto acertó el motor en el pasado?

**Principios de diseño:**
- **Evidencia antes que opinión.** Los resultados históricos se validan por mitades de la muestra (`**` robusto, `*` coherente) y los modelos predictivos se miden fuera de muestra. Si un modelo no supera a la media histórica, el informe dice "no usar".
- **Sin información del futuro.** Las historias usan solo lo publicado en cada fecha, con los retrasos de publicación de cada dato.
- **Todo opcional y tolerante a fallos.** Si una fuente falla, el informe se genera igual y la sección «Salud de los datos» dice qué falló.
- **Sin dependencia de IA ni de servicios de pago.** Funciona sin Claude.
- **Datos, no consejo.** Las señales son sistemáticas y transparentes, y las decisiones son del usuario.

Proyecto de partida: un *fork* del repositorio "Macro Shock Risk Engine" (análisis de comunicados de bancos centrales con puntaje de riesgo y árbol de escenarios). Sobre esa base se construyó todo lo demás (ver [CAMBIOS.md](CAMBIOS.md)).

## 2. Qué hay (versión 1.2)

| Área | Comando / sección del informe | Módulo (`python/src/macro_shock/`) |
|---|---|---|
| Análisis de un comunicado de banco central (motor original): tono, puntaje de riesgo 0–100, escenarios, prueba de estrés de la cartera | `analyze` | `nlp/`, `risk_scoring/`, `scenario_engine/`, `portfolio_impact/` |
| Régimen de riesgo (4 regímenes, con los índices de estrés de la Fed) | `daily`, informe «Régimen de riesgo» | `market_context/vulnerability_scorer.py`, `research/regime_history.py` |
| Informe diario con alertas y registro CSV | `daily` | `research/daily.py` |
| Estudio FOMC (bitcoin, S&P 500, VIX) | `fomc-study` | `research/fomc_study.py` |
| Sectores y estilos por régimen desde 1990, con robustez por mitades | `sector-study`, «Evidencia sectorial» | `research/sector_study.py`, `research/sector_regimes.py`, `research/french.py` |
| Optimizador (paridad de riesgo, mínima varianza, límite de caída) | `optimize` | `research/allocation.py`, `portfolio_impact/optimizer.py` |
| Nowcast del IPC por subpartidas energéticas | `nowcast`, «Energía e IPC» | `research/cpi_nowcast.py` |
| Escenario que descuenta el mercado, crecimiento, cuadrante macro y sectores por cuadrante desde 1972 | `macro`, «Qué descuenta el mercado», «Crecimiento, inflación y cuadrante macro» | `research/macro_scenario.py`, `research/growth.py`, `research/macro.py` |
| Informe ejecutivo HTML (página 1 con visión por activo, calendario, gráficas) | `report` | `research/report.py`, `research/charts.py`, `research/view.py`, `research/agenda.py` |
| Análogos históricos y modelo predictivo validado fuera de muestra | informe | `research/analogs.py` |
| Eje cripto (10 monedas, funding, sentimiento, stablecoins, régimen) | `crypto`, «Cripto» | `research/crypto.py` |
| Liquidez de la Fed y expectativas de tipos | informe | `research/liquidity.py` |
| Carry del yen | informe, alerta en `daily` | `research/carry.py` |
| Sectores del S&P 500 (11 ETF) con sensibilidades y mapa de escenarios | informe | `research/sectors.py`, `research/stooq.py` |
| Metales (oro, plata y cobre) | informe | `research/metals.py` |
| Estructura del VIX | informe, alerta en `daily` | `research/view.py` |
| Valoración de la bolsa (PER aproximado, Buffett, prima de riesgo) | informe | `research/valuation.py` |
| Posicionamiento CFTC | informe | `research/positioning.py` |
| Historial de aciertos | informe (`reports/predicciones.csv`) | `research/track_record.py` |
| Salud de los datos | informe | `research/data_health.py` |
| Instalador para Windows y acceso directo | `scripts/install.ps1`, `scripts/report.ps1` | — |
| Tests automáticos en GitHub (Ubuntu 3.11/3.13/3.14 y Windows 3.14) | `.github/workflows/tests.yml` | `python/tests/unit/` (417 tests) |

## 3. Fuentes de datos

| Fuente | Datos | Clave | Probada con datos reales |
|---|---|---|---|
| FRED (Fed de St. Louis) | Tasas, crédito, VIX, S&P 500, Nasdaq, dólar, energía, IPC, empleo, encuestas regionales, liquidez, yen, valoración Z.1 | Gratuita (`.env`) | Sí, salvo `VXVCLS`, las series de valoración y el calendario de publicaciones |
| Kenneth French Data Library | Sectores, factores y momentum diarios desde 1926 | No | Sí |
| Binance (spot y futuros) | Precios cripto desde 2017, funding, interés abierto; perpetuos de oro y plata | No | Sí (cripto). Metales pendiente |
| CoinGecko, alternative.me, DefiLlama | Capitalización y dominancia, Fear & Greed, stablecoins | No | Sí |
| Stooq | ETF de sectores, SPY, oro, plata y cobre | No | **Pendiente** |
| CFTC (API pública) | Posicionamiento semanal | No | **Pendiente** |

## 4. Qué falta y limitaciones conocidas

| Falta | Por qué / alternativa |
|---|---|
| PMI de S&P Global e ISM | No son públicos. Las encuestas de Filadelfia y Nueva York dan solo la dirección |
| BPA del S&P 500 y revisiones de analistas | Son de pago. La valoración usa los beneficios agregados del BEA |
| Escenarios con probabilidades | Requieren calibración: el historial de aciertos necesita unos meses de datos |
| Validación del motor original (`analyze`) con comunicados reales | El análisis de texto busca frases exactas y casi siempre da `neutral` con textos reales; está sin validar |
| Europa, China y emergentes | Solo EE. UU. (más el yen) |
| Volatilidad de bonos (MOVE) y *skew* | No están en fuentes gratuitas fiables |
| Flujos de ETF (por ejemplo, ETF de bitcoin) | No hay fuente gratuita estable |
| Avisos por correo o Telegram | No implementado |
| Caché local de datos | Cada informe vuelve a descargar todo (1–3 minutos) |
| Nowcast de electricidad y gas por tubería | No supera a la estimación ingenua (tarifas reguladas); pesa unos 0.01 puntos en el IPC |

## 5. Fórmulas y supuestos a revisar

Puntos donde el motor depende de una decisión de diseño o de una aproximación. Conviene revisarlos con datos reales antes de darles mucho peso.

| # | Dónde | Supuesto | Qué revisar |
|---|---|---|---|
| 1 | `vulnerability_scorer.py` | Pesos de los componentes (liquidez 0.25, volatilidad 0.20, crédito 0.20, curva 0.10, amplitud 0.10, financiación 0.10, posicionamiento 0.05), umbrales de régimen y nodos de calibración, **heredados del proyecto original** | Recalibrar con la historia: ¿separan bien los regímenes los rendimientos posteriores? |
| 2 | `regime_history.py` | Antes de 2023 el crédito se aproxima con BAA10Y por mapeo de cuantiles (coincide un 76% con el ICE real) | Ver si otro sustituto, como el spread AAA−BAA, coincide más |
| 3 | `cpi_nowcast.py` | MCO con efectos mensuales; pesos del IPC por mínimos cuadrados no negativos en 60 meses; subyacente y alimentos = media de 3 meses | Validación en tiempo real con el historial de aciertos; añadir un nowcast de la subyacente |
| 4 | `growth.py` | Ejes con pesos iguales; retrasos de publicación aproximados (paro 5 días, empleo 10, IPC 17, producción industrial 21); escala robusta con tope ±3 | Sensibilidad del cuadrante a esos retrasos y pesos |
| 5 | `macro_scenario.py` | Patrones de signo de los 4 escenarios; umbrales 0.35 / 0.15 / −0.15; ventana de 20 días; escala de 3 años | Comparar los escenarios "confirmados" con lo que pasó después |
| 6 | `view.py` | `PLAYBOOK`: dirección de cada activo por escenario (manual estándar, criterio experto) y umbrales ±2 | Validar con la historia o con el historial de aciertos |
| 7 | `analogs.py` | Distancia euclídea con pesos iguales en 7 variables; ridge λ = 5. R² fuera de muestra real −0.14 (no usar) | Selección de variables; otros horizontes |
| 8 | `valuation.py` | El valor de mercado Z.1 incluye todas las empresas de EE. UU. (no solo el S&P 500); beneficios del BEA después de impuestos sin ajustes; la prima de riesgo usa beneficios pasados | Comparar el percentil con el CAPE de Shiller |
| 9 | `sectors.py` | Betas por regresión múltiple en periodos de 20 días durante 5 años (unas 63 observaciones, con ruido); "movimiento típico" = 1 desviación | Estabilidad de las betas, por ejemplo por mitades |
| 10 | `crypto.py` | Régimen = tendencia de 200 días × volatilidad de 30 días frente a su mediana anual; umbrales de funding de +30% y −5% anual | Verificar con episodios de liquidaciones |
| 11 | `carry.py` | Desmontaje = yen +3% en 10 días hábiles; tasas de la OECD con unos 2.5 meses de retraso | Umbral; usar tasas a 2 años de Japón si hay fuente |
| 12 | `positioning.py` | Códigos de contrato de la CFTC (13874A, 043602, 097741, 067651, 088691, 085692, 133741); índice COT mínimo-máximo de 3 años | **Verificar los códigos en la primera ejecución** |
| 13 | `metals.py` | Oro frente a tasa real: regresión lineal de 3 años del logaritmo del oro sobre el TIPS a 10 años | La relación se rompió en 2022–2024 (compras de bancos centrales) |
| 14 | `scenario_tree.py` | Beta de bitcoin 1.0 y beta en colas 1.16 (medidas en 2021–2025) | Recalcular cada año con `fomc-study` |
| 15 | `agenda.py` | Fechas de la Fed de 2026 escritas a mano | Verificar en [federalreserve.gov](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) y añadir las de 2027 |
| 16 | `fomc_study.py` | Fechas FOMC 2017–2025 escritas a mano | Añadir las nuevas cada año (`FOMC_ANNOUNCEMENTS`) |

## 6. Cómo podría evolucionar

Por orden de valor, todo con fuentes gratuitas:

1. **Escenarios con probabilidades** (base / alcista / bajista), calibradas con el historial de aciertos.
2. **Validación del motor original** con los comunicados reales de la Fed desde 2017, y **sorpresa frente al mercado** en `analyze` usando las expectativas (2 años − fed funds) del informe.
3. **Recalibración del clasificador de riesgo** (punto 1 de la sección 5) con la historia, añadiendo divisas y materias primas.
4. **Caché local de datos** (por ejemplo, Parquet en `data/cache/`): informes en segundos y funcionamiento aunque una fuente caiga.
5. **Avisos automáticos** por correo o Telegram cuando `daily` genere alertas; informe programado antes de la apertura.
6. **Europa y China**: IPC armonizado y tipos del BCE (portal de datos del BCE), y el yuan.
7. **Nowcast de la subyacente** con alquileres (índices de alquiler) y precios de coches usados.
8. **Interfaz web local** (por ejemplo, con la API `serve` que ya existe) para consultar el historial de informes.

## 7. Contexto para continuar en otra conversación

**Repositorio:** `yohancristobalr-code/Macro-Shocks-Portfolio-Risk-Engine` (el upstream `Josh20241995` es de otra persona: no enviar cambios allí). El proyecto Python está en la carpeta interior `Macro-Shocks-Portfolio-Risk-Engine/`.

**Forma de trabajo:**
- Cada mejora va en un **pull request** a `main` desde una rama `claude/...`. El usuario lo une cuando el CI de GitHub está en verde.
- Tests: `python -m pytest python/tests/unit -q` desde la carpeta interior. Todos usan datos sintéticos con respuesta conocida y no necesitan red ni clave.
- Cada módulo nuevo lleva sus **tests con un efecto plantado** que el código debe recuperar, **validación fuera de muestra** si predice algo, y **documentación en español** (`GUIA_USO.md`, `RUTINA_DIARIA.md` y este documento).
- La salida de la consola está en inglés y el informe HTML y la documentación en español.
- Python ≥ 3.11 y sin dependencias nuevas salvo que sea imprescindible: las gráficas son SVG propias y las APIs se llaman con `urllib`.

**Sobre el usuario:**
- Habla español y usa Windows con PowerShell.
- Hay que darle los comandos **de uno en uno por línea**, porque PowerShell no admite `&&` como bash.
- Trabaja desde `$HOME\Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine`.
- Opera bolsa de EE. UU., sectores, oro y cripto (sigue BTC, ETH, SOL, AAVE y ONDO), con un límite de caída del 25%.
- Prepara informes para una junta; su escenario de referencia está en la tabla de escenarios del informe.

**Seguridad:**
- La clave de FRED vive solo en `.env`, que está en `.gitignore`. **Nunca debe pegarse en un chat.**
- `portfolio.csv` y `reports/` son privados y también están en `.gitignore`.
- Ninguna API usada necesita permisos de trading. Si algún día se conecta un exchange, solo con claves de lectura.

**Pendiente de verificar con datos reales** (primera ejecución tras la versión 1.2): Stooq, perpetuos de metales de Binance, API de la CFTC (códigos de contrato), `VXVCLS`, las series de valoración Z.1 y el calendario de FRED. La sección «Salud de los datos» del informe lo mostrará.

**Mensaje para empezar otra conversación** (copiar y pegar):

> Continúo el desarrollo del Macro Shock Risk Engine, repositorio `yohancristobalr-code/Macro-Shocks-Portfolio-Risk-Engine`. Lee primero `Macro-Shocks-Portfolio-Risk-Engine/docs/PROYECTO.md` (contexto, estado, fórmulas a revisar y hoja de ruta) y `docs/CAMBIOS.md`. Trabaja en español, con un PR por mejora, tests con datos sintéticos y documentación actualizada. Comandos para Windows PowerShell, uno por línea. Lo siguiente que quiero hacer es: …
