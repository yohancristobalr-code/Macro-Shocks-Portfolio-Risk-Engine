# Comandos para ejecutar el motor y generar el informe HTML

Hoja de referencia para Windows PowerShell, versión 1.2.1. **Escribe un comando por línea**: PowerShell no acepta varios comandos unidos con `&&`.

La explicación de cada resultado está en [GUIA_USO.md](GUIA_USO.md), y la rutina diaria en [RUTINA_DIARIA.md](RUTINA_DIARIA.md).

---

## 1. Primera vez (instalación)

Necesitas [Python 3.11 o superior](https://www.python.org/downloads/) (marca "Add python.exe to PATH" al instalarlo), [Git](https://git-scm.com/download/win) y una [clave gratuita de FRED](https://fred.stlouisfed.org/docs/api/api_key.html).

```powershell
cd $HOME
git clone -c core.autocrlf=false https://github.com/yohancristobalr-code/Macro-Shocks-Portfolio-Risk-Engine.git
cd Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

El instalador hace lo siguiente:
1. Crea el entorno `.venv`.
2. Instala el motor.
3. Pide la clave de FRED (oculta) y la guarda en `.env`.
4. Ejecuta los tests.
5. Crea el acceso directo **"Informe Macro"** en el escritorio.

## 2. Actualizar a la última versión

```powershell
cd $HOME\Macro-Shocks-Portfolio-Risk-Engine
git pull
cd Macro-Shocks-Portfolio-Risk-Engine
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Volver a ejecutar el instalador conserva tu clave, instala lo nuevo y comprueba que todo pasa los tests.

## 3. Generar el informe HTML completo

**La forma más fácil:** doble clic en **"Informe Macro"**, en el escritorio. Tarda 1–3 minutos y se abre en el navegador.

**Desde PowerShell:**

```powershell
cd $HOME\Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine
.\.venv\Scripts\Activate.ps1
python -m macro_shock report --open
```

- El informe se guarda en `reports\informe_AAAA-MM-DD.html`. Puedes abrirlo cuando quieras y no necesita internet.
- Cada ejecución registra sus previsiones en `reports\predicciones.csv` para el historial de aciertos.
- Si instalaste el motor sin el instalador (`pip install -e .`), omite la línea `Activate.ps1`.

**Variantes:**

```powershell
python -m macro_shock report --quick --open
python -m macro_shock report --output C:\Informes\hoy.html --open
```

| Opción | Efecto |
|---|---|
| `--open` | Abre el informe en el navegador al terminar |
| `--quick` | Más rápido: omite los sectores por régimen y por cuadrante, los análogos y el modelo |
| `--output RUTA` | Guarda el HTML donde indiques |

**Qué contiene el informe:**
- página 1: visión por activo, sectores preferidos y calendario de 14 días;
- lectura ejecutiva;
- mercado, crecimiento e inflación;
- IPC, riesgo, valoración y posicionamiento;
- sectores del S&P 500, metales, análogos y modelo;
- cripto, liquidez de la Fed y carry del yen;
- historial de aciertos y salud de los datos.

## 4. Comandos individuales (salida en la consola)

Con el entorno activado (`.\.venv\Scripts\Activate.ps1`):

| Comando | Para qué |
|---|---|
| `python -m macro_shock daily` | Informe diario con régimen, alertas, energía, IPC estimado y escenario macro |
| `python -m macro_shock daily --quick` | Lo mismo, más rápido (sin sectores ni IPC) |
| `python -m macro_shock macro` | Escenario que descuenta el mercado, crecimiento, inflación, cuadrante y sectores por cuadrante |
| `python -m macro_shock macro --quick` | Lo mismo sin los sectores (útil antes del PMI) |
| `python -m macro_shock nowcast` | IPC del próximo mes estimado por subpartidas energéticas |
| `python -m macro_shock crypto` | Las 10 criptos, funding, sentimiento, stablecoins y liquidez de la Fed |
| `python -m macro_shock sector-study` | Sectores y estilos por régimen desde 1990 |
| `python -m macro_shock optimize --max-drawdown 0.25` | Pesos de cartera con tu límite de caída |
| `python -m macro_shock fomc-study` | Reacción de bitcoin, la bolsa y el VIX a la Fed |
| `python -m macro_shock demo --all` | Escenarios de demostración (no necesita datos) |
| `python -m pytest python/tests/unit -q` | Comprobar que todo funciona (debe terminar en `passed`) |

**Analizar un comunicado de la Fed**, con prueba de estrés de tu cartera, sectores y contraste de inflación (en una sola línea):

```powershell
python -m macro_shock analyze --title "FOMC" --institution "Federal Reserve" --event-time "2026-10-28T14:00:00-04:00" --data fred --text "TEXTO DEL COMUNICADO EN INGLÉS" --portfolio portfolio.csv --sectors --inflation
```

En `--event-time`, usa `-04:00` en horario de verano de EE. UU. y `-05:00` en invierno.

## 5. Generar el informe automáticamente cada mañana

En una sola línea, para generarlo de lunes a viernes a las 8:15:

```powershell
schtasks /Create /TN "MSRE Informe" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 08:15 /TR "powershell -NoProfile -ExecutionPolicy Bypass -File $HOME\Macro-Shocks-Portfolio-Risk-Engine\Macro-Shocks-Portfolio-Risk-Engine\scripts\report.ps1"
```

- **Probarlo ahora:** `schtasks /Run /TN "MSRE Informe"`.
- **Eliminarlo:** `schtasks /Delete /TN "MSRE Informe" /F`.
- La tarea solo se ejecuta si tu sesión de Windows está iniciada, y abre el informe en el navegador al terminar.

## 6. Si algo falla

| Mensaje | Solución |
|---|---|
| `FRED_API_KEY is not set` | Ejecuta desde la carpeta interior, o vuelve a ejecutar `install.ps1` para guardar la clave |
| `No module named macro_shock` | Activa el entorno: `.\.venv\Scripts\Activate.ps1` |
| `ejecución de scripts deshabilitada` | Usa `powershell -ExecutionPolicy Bypass -File ...` como en los ejemplos, o ejecuta una vez `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Una sección dice "no disponible" | Una fuente falló ese día. El resto del informe es válido, y la sección «Salud de los datos» dice cuál falló |
| `section '...' incomplete` en la consola | Lo mismo: aviso informativo, el informe se generó |

**Nunca pegues tu clave de FRED en un chat ni en un documento.** Vive solo en el archivo `.env`.
