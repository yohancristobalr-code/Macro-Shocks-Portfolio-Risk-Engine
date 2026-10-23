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

### 5.1 `daily` — informe diario

```powershell
python -m macro_shock daily
python -m macro_shock daily --quick     # sin el estudio sectorial (unos segundos)
```

Muestra los datos del día, el **régimen**, las **alertas** frente a la ejecución anterior y la evidencia sectorial para el régimen. Guarda:

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

- **`Reliability: LOW`** es normal con FRED: solo cubre 4 de los 8 tipos de datos de mercado.
- **`YOUR PORTFOLIO`**: ganancia o pérdida esperada, la del peor 5% de los casos, y qué posiciones pierden más en el peor escenario.
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
- **Hallazgos de la ejecución de octubre de 2026:** el único robusto fue el **momentum en `fragile_risk_on`**. Tras la tensión (`risk_off_correction`), la tecnología tendió a superar al mercado y las eléctricas a quedarse atrás: es el efecto rebote.

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
  | Estudios (FOMC, sectores, regímenes, cartera) | `python/src/macro_shock/research/` |
  | Escenarios y beta de bitcoin | `python/src/macro_shock/scenario_engine/scenario_tree.py` |
  | Comandos | `python/src/macro_shock/__main__.py` |

  Cada cambio debería ir acompañado de su test en `python/tests/unit/`.
