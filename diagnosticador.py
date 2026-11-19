# -*- coding: utf-8 -*-
"""
DIAGNOSTICADOR DE MERCADOS
Genera docs/index.html con tres pestañas: Diagnosticador · Opciones · Calendario.
Se ejecuta cada día desde GitHub Actions. Necesita el secreto TIINGO_API_KEY.
"""

import os
import re
import io
import json
import math
import time
import html
import datetime as dt

import numpy as np
import pandas as pd
import requests


# ----------------------------------------------------------------------------
# TEXTOS DE AYUDA (ⓘ)
# ----------------------------------------------------------------------------
# Textos de ayuda (ⓘ). Clave -> (título, párrafos/viñetas en HTML, [(fuente, url)])

FRED = "https://fred.stlouisfed.org/series/"

INFO = {
    # ---------------- DIAGNOSTICADOR: tarjetas y reglas ----------------
    "valoracion": ("Valoración de la bolsa de EE. UU. (ECY)", """
<p><b>Qué es.</b> El <i>Excess CAPE Yield</i> (ECY) de Shiller: la rentabilidad por beneficios ajustada al ciclo (1/CAPE) menos el tipo real del bono a 10 años. Mide cuánta prima ofrece la bolsa de EE. UU. frente a los bonos.</p>
<ul><li>&lt;1,5 %: muy cara · 1,5–3 %: cara · 3–4,5 %: razonable · ≥4,5 %: barata.</li>
<li>Una caída fuerte de la bolsa sube el ECY.</li></ul>
<p><b>Cómo se usa.</b> Solo afecta al S&amp;P 500 y al S&amp;P 500 equiponderado: para que sus condiciones sean "muy buenas" hace falta un ECY de al menos el 3 %.</p>
<p><b>Cuidado.</b> Mide valoración a 10 años; no sirve para elegir el momento a pocos meses. Para Europa, emergentes y MSCI World no hay una medida de valoración gratuita equivalente.</p>""",
        [("Shiller, Black y Jivraj (2020), CAPE and the COVID-19 pandemic effect", "https://spiral.imperial.ac.uk/handle/10044/1/86992"),
         ("Datos de Shiller", "https://shillerdata.com/"),
         ("AAII: ECY y rentabilidades a 10 años", "https://www.aaii.com/investor-update/article/235947-excess-cape-yield-low-ahead-of-potential-interest-rate-cut")]),

    "condiciones": ("Condiciones de indexación", """
<p>Valoración, para cada índice, de si es buen momento para tenerlo indexado. Se decide en este orden:</p>
<ul><li><b>Tendencia</b> (media de 40 semanas): alcista si el precio está por encima de su media y esta sube; bajista si está por debajo y baja; si no, lateral.</li>
<li><b>Muy malas:</b> tendencia bajista y, además, crédito deteriorándose con rapidez o precio todavía a más de un 10 % de su media de 200 semanas (le queda recorrido a la baja).</li>
<li><b>Malas:</b> tendencia alcista pero con señales de fin de ciclo (fase de madurez o giro, o 2 señales de agotamiento); o sin tendencia, con el impulso débil y lejos de la zona de suelo.</li>
<li><b>Regular-pesimista:</b> bajista pero ya cerca de la zona de suelo, sin señales suficientes; o sin tendencia y con el impulso débil.</li>
<li><b>Regular-optimista:</b> sin tendencia pero con el impulso mejorando; tendencia alcista pero perdiendo fuerza frente a la liquidez (fase de rezagado o suelo en el mapa); o bajista cerca del suelo con la puerta en tramo 2 o más.</li>
<li><b>Buenas:</b> tendencia alcista sana: fase de arranque o aceleración en el mapa y sin señales de fin de ciclo.</li>
<li><b>Muy buenas:</b> lo anterior y, además, precio atractivo: como mucho a un 10 % de su media de 200 semanas y, en los índices de EE. UU., ECY de al menos el 3 %.</li></ul>
<p><b>Puerta:</b> puntos de suelo y tramo que corresponde construir. <b>Cuidado:</b> reglas de criterio propio, no calibradas.</p>""", []),

    "puntos": ("Puerta de indexación", """
<p><b>Qué es.</b> Suma de condiciones típicas de suelo de mercado, cumplidas en cualquier orden en las últimas 13 semanas. Se calcula para cada índice:</p>
<ul><li>Precio del índice cerca de su media de 200 semanas: 2 puntos.</li>
<li>Crédito girando a la baja desde su pico: 2 puntos (común a todos).</li>
<li>VIX vuelve a contango tras inversión: 1 punto (común a todos).</li>
<li>RSI semanal del índice en sobreventa: 1 punto.</li></ul>
<p>2, 4 y 6 puntos abren los tramos 1, 2 y 3: construir la posición en tercios. Si una condición deja de cumplirse no se abren tramos nuevos, pero no hay venta automática.</p>
<p><b>Cuidado.</b> Pesos y umbrales son de criterio propio, no calibrados: hay pocos mercados bajistas con datos.</p>""", []),

    "veto": ("Veto por crédito", """
<p><b>Qué es.</b> Freno de emergencia: si el diferencial de crédito (Baa − bono 10 años) sube un 30 % o más en 13 semanas y además acelera, el tramo máximo de la puerta queda en 1 y los índices en tendencia bajista pasan a condiciones "muy malas".</p>
<p><b>Por qué.</b> En 2001-02 y 2008-09 el S&amp;P 500 perforó su media de 200 semanas y siguió cayendo meses mientras el crédito se deterioraba. El veto evita comprar demasiado pronto en ese escenario.</p>
<p>Se levanta tras 4 semanas seguidas sin cumplirse.</p>""",
        [("Serie BAA10Y (FRED)", FRED + "BAA10Y")]),

    "conviccion": ("Grado de convicción", """
<p><b>Qué es.</b> Cuántos bloques independientes (régimen, estrés, liquidez y valoración/técnico/sentimiento) apuntan en la misma dirección que el mapa de la renta variable global.</p>
<ul><li>0–1 coincidencias: baja → no construir.</li><li>2–3: media → completar el tramo en 2–3 meses.</li><li>4: alta → completar el tramo en 1 mes.</li></ul>
<p><b>Cuidado.</b> Regla de criterio propio. A 6-12 meses ninguna señal tiene gran capacidad predictiva.</p>""", []),

    "c_ma200": ("Precio cerca de la media de 200 semanas", """
<p>La media de 200 semanas (unos 4 años) es una referencia de muy largo plazo. Históricamente los grandes suelos se han formado cerca de ella o por debajo.</p>
<p><b>En esta web.</b> Se cumple si el cierre semanal del índice está como mucho un 5 % por encima de su propia media de 200 semanas, o por debajo, en alguna de las últimas 13 semanas. Al lado se muestra la distancia actual.</p>
<p><b>Cuidado.</b> En 2001-02 y 2008-09 el precio la perforó y siguió cayendo meses (de ahí el veto por crédito).</p>""", []),

    "c_credito": ("Crédito girando a la baja desde su pico", """
<p>Cuando el diferencial de crédito empieza a bajar tras un episodio de estrés, suele indicar que lo peor de la crisis financiera ha pasado.</p>
<p><b>En esta web</b> (serie Baa − bono 10 años), deben cumplirse tres cosas: hubo estrés (máximo de 26 semanas ≥ 1,4 veces el mínimo del año anterior), el diferencial ha bajado un 15 % desde ese máximo y su media de 4 semanas está por debajo de la de 13.</p>""",
        [("Serie BAA10Y (FRED)", FRED + "BAA10Y")]),

    "c_vix": ("VIX vuelve a contango tras inversión", """
<p>Normalmente la volatilidad esperada a 3 meses (VIX3M) es mayor que la de 1 mes (VIX): eso es "contango". En pánicos se invierte (VIX &gt; VIX3M). La vuelta a la normalidad suele acompañar el final de la fase aguda de miedo.</p>
<p><b>En esta web.</b> Al menos una semana con VIX/VIX3M &gt; 1 y después dos cierres semanales seguidos por debajo de 0,95.</p>""",
        [("VIX3M (Macroption)", "https://www.macroption.com/vix3m/")]),

    "c_rsi": ("RSI semanal en sobreventa", """
<p>El RSI (índice de fuerza relativa, J. Welles Wilder, 1978) compara subidas y bajadas recientes en una escala de 0 a 100. Por debajo de 30 se considera sobreventa.</p>
<p><b>En esta web.</b> RSI de 14 semanas de cada índice por debajo de 30 en alguna de las últimas 13 semanas. En datos semanales es una señal rara, típica de caídas fuertes.</p>""", []),

    "b_A": ("Bloque A · Régimen", """
<p>Dirección de la economía. Vota a favor del riesgo si las encuestas industriales de la Fed (Filadelfia y Nueva York) mejoran a 3 meses y las peticiones de subsidio bajan. Vota en contra si la regla de Sahm llega a 0,5 o si ambas empeoran.</p>""", []),
    "b_B": ("Bloque B · Estrés", """
<p>Tensión financiera. A favor del riesgo si el diferencial de crédito baja a 13 semanas y el VIX está en contango. En contra si el diferencial sube y el VIX está invertido.</p>""", []),
    "b_C": ("Bloque C · Liquidez", """
<p>A favor del riesgo si la liquidez neta de la Fed sube a 13 semanas y las condiciones financieras (NFCI) se relajan. En contra si ocurre lo contrario.</p>
<p><b>Cuidado.</b> La relación liquidez-bolsa es una idea muy popular desde 2020, con poca evidencia académica sólida.</p>""", []),
    "b_EFG": ("Bloque E+F+G · Valoración, técnico y sentimiento", """
<p>Usa el indicador de sentimiento (réplica del Euphoriameter). Por encima de 80 vota en contra del riesgo (euforia); por debajo de 20, a favor (miedo).</p>""", []),
    "b_D": ("Bloque D · Beta o alfa", """
<p>No vota. Indica si el entorno favorece seleccionar valores o comprar el índice.</p>
<ul><li>Dispersión alta (DSPX) y correlación baja (COR3M): los valores se mueven distinto entre sí → más margen para la selección (alfa).</li>
<li>Lo contrario: todo se mueve junto → el índice (beta) es más eficiente.</li></ul>
<p><b>Cuidado.</b> Es el bloque con evidencia más débil: los informes SPIVA muestran que la mayoría de gestores activos no bate al índice a largo plazo.</p>""",
        [("Cboe: correlación implícita", "https://cboe.com/us/indices/implied/"),
         ("Cboe: índice de dispersión", "https://www.cboe.com/us/indices/dispersion")]),
    "sentimiento": ("Sentimiento (réplica del Euphoriameter)", """
<p>Media de tres percentiles: valoración (CAPE), calma (VIX invertido) y distancia del ACWI a su media de 200 semanas. De 0 (miedo) a 100 (euforia).</p>
<p><b>Por qué una réplica.</b> El Euphoriameter original no tiene datos descargables, y el índice NAAIM, que iba a usarse, pasó a ser de pago.</p>
<p><b>Cuidado.</b> Sus componentes se solapan con la valoración y el técnico: cuenta como un único input.</p>""", []),

    # ---------------- Mapas ----------------
    "mapa": ("Mapa de rotación de motores", """
<p>Versión abierta y aproximada de los <i>Relative Rotation Graphs</i> de Julius de Kempenaer. Cada punto es un motor de rentabilidad, comparado con las letras del Tesoro (BIL).</p>
<ul><li><b>Eje horizontal (fuerza):</b> por encima de 100, el motor bate a la liquidez en su tendencia reciente.</li>
<li><b>Eje vertical (impulso):</b> por encima de 100, esa fuerza está acelerando.</li>
<li>El giro habitual es en sentido horario: mejorando → liderando → debilitándose → rezagado.</li>
<li>La cola muestra las últimas 24 semanas (cierres semanales): el cuadrado es el punto de partida y el círculo grande, el momento actual.</li>
<li>Los colores indican la familia del motor (renta variable, renta fija, materias primas…), no su fase. Puedes ocultar familias o motores con las casillas.</li></ul>
<p><b>Cuidado.</b> Los proxies son fondos cotizados en dólares; no reproduce exactamente el RRG comercial (es una marca registrada).</p>""",
        [("StockCharts ChartSchool: RRG", "https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-types/relative-rotation-graphs-rrg-charts"),
         ("StockCharts: patrón de rotación", "https://c.stockcharts.com/articles/chartwatchers/2019/04/when-tails-remain-on-one-side-of-a-relative-rotation-graph-what-does-that-mean.html")]),
    "fuerza": ("Fuerza relativa", """
<p>Compara el motor con las letras del Tesoro (liquidez en dólares): se divide su precio entre el de las letras y se mira si esa relación está por encima o por debajo de su media de 26 semanas.</p>
<p><b>Lectura.</b> Por encima de 100, el motor está batiendo a la liquidez en su tendencia reciente; por debajo de 100, lo hace peor que tener el dinero en liquidez.</p>""", []),
    "impulso": ("Impulso", """
<p>Velocidad a la que cambia la fuerza relativa: compara la fuerza actual con la de hace 4 semanas.</p>
<p><b>Lectura.</b> Por encima de 100, la fuerza está mejorando (acelera); por debajo, empeorando (frena). El impulso suele girar antes que la fuerza: por eso anticipa las rotaciones.</p>""", []),
    "agotamiento": ("Señales de agotamiento", """
<p>Cuántas de estas tres señales de extremo se dan ahora (de 0 a 3):</p>
<ul><li>RSI semanal por encima de 70 (alcista) o por debajo de 30 (bajista).</li>
<li>Distancia a su media de 40 semanas entre el 10 % más alto (alcista) o más bajo (bajista) de su historia.</li>
<li>Fuerza relativa por encima de su percentil 90 (alcista) o por debajo del 10 (bajista).</li></ul>
<p><b>Lectura.</b> "Alcista 2 de 3" indica una subida muy estirada: riesgo de fin de ciclo. "Bajista 2 de 3" indica una caída muy estirada: posible suelo.</p>""", []),
    "direccion": ("Dirección del mapa (RV global)", """
<p>Fase en la que está el ACWI (bolsa mundial) en el mapa de rotación. Suelo, arranque o aceleración cuentan como dirección favorable al riesgo; madurez, giro o rezagado, como desfavorable. La convicción mide cuántos bloques coinciden con esta dirección.</p>""", []),
    "top_salir": ("Cerca de fin de ciclo · ir saliendo", """
<p>Motores en fase de <b>giro</b> (fuerza alta pero impulso ya por debajo de 100) o de <b>madurez</b> (impulso frenando 3 semanas o 2 señales de agotamiento alcista).</p>
<p>Orden: primero los que están en giro y, dentro de cada fase, los que más impulso han perdido en las últimas 4 semanas.</p>""", []),
    "top_ampliar": ("Tendencia alcista con recorrido · ampliar", """
<p>Motores en fase de <b>aceleración</b> (fuerza e impulso por encima de 100, sin frenar) y con como mucho una señal de agotamiento alcista: la tendencia está viva y no parece estirada.</p>
<p>Orden: de mayor a menor impulso.</p>""", []),
    "top_construir": ("Podrían iniciar tendencia alcista · empezar a construir", """
<p>Motores en fase de <b>arranque</b>: todavía lo hacen peor que la liquidez (fuerza por debajo de 100), pero su impulso ya es positivo. Es donde está el margen: al principio del ciclo.</p>
<p>Orden: de mayor a menor impulso. Recuerda construir poco a poco: muchos arranques fallan.</p>""", []),
    "nivel_macro": ("Nivel (percentil histórico)", """
<p>Qué parte de toda la historia de la variable queda por debajo de su valor actual. 0 = mínimo histórico, 100 = máximo histórico, 50 = mitad de su rango habitual.</p>""", []),
    "impulso_macro": ("Impulso de una variable macro", """
<p>Cambio de la variable en las últimas 13 semanas (datos semanales y diarios) o en los últimos 6 meses (datos mensuales). En el mapa se expresa como percentil: por encima de 50, la variable sube más deprisa de lo habitual; por debajo, baja o sube menos de lo habitual.</p>""", []),
    "fases": ("Fases y qué hacer", """
<ul><li><b>Suelo:</b> fuerza e impulso bajos, pero el impulso sube 3 semanas o hay agotamiento bajista → vigilar.</li>
<li><b>Arranque:</b> fuerza baja pero impulso al alza → construir poco a poco (el margen está al principio).</li>
<li><b>Aceleración:</b> fuerza e impulso altos → mantener.</li>
<li><b>Madurez:</b> fuerza alta pero impulso frenando 3 semanas, o 2 señales de agotamiento → empezar a salir antes del clímax.</li>
<li><b>Giro:</b> fuerza alta e impulso bajo → salir.</li>
<li><b>Rezagado:</b> fuerza e impulso bajos sin señal de suelo → no tocar.</li></ul>
<p><b>Agotamiento (↑/↓):</b> número de señales de extremo alcista/bajista: RSI semanal &gt;70 o &lt;30, distancia a la media de 40 semanas en el 10 % extremo de su historia, fuerza relativa en el percentil &gt;90 o &lt;10.</p>""", []),
    "mapa_macro": ("Mapa macro", """
<p>Sitúa cada variable por su <b>nivel</b> (percentil histórico, eje horizontal) y su <b>impulso</b> (percentil de su cambio reciente, eje vertical). Sirve para ver en qué fase está cada pata del régimen: si está alta y todavía subiendo, alta y frenando, etc.</p>
<p>Una variable alta y subiendo puede estar cerca de su clímax: el margen está en anticipar el giro, no en seguirla.</p>""", []),
    "variables": ("Variables", """
<p><b>Percentil:</b> qué parte de su historia queda por debajo del valor actual (100 = máximo histórico).</p>
<p><b>Cambio reciente:</b> 13 semanas para los datos rápidos y 6 meses para los mensuales.</p>
<p><b>Fase:</b> misma lógica que los motores, aplicada a nivel e impulso. "Madurez" = nivel alto (percentil &gt;80) con el impulso frenando.</p>
<p>⚠ = el dato lleva más tiempo del normal sin actualizarse.</p>""", []),

    # ---------------- Variables macro ----------------
    "Inflación (IPC interanual, %)": ("Inflación (IPC interanual)", """
<p>Variación anual del índice de precios al consumo de EE. UU. (todos los artículos, desestacionalizado). Mensual, con unas 2 semanas de retraso.</p>
<p><b>Lectura.</b> Lo relevante no es el nivel sino la dirección: inflación acelerando favorece activos reales (materias primas, energía); frenando favorece bonos y crecimiento.</p>""",
        [("Serie CPIAUCSL (FRED)", FRED + "CPIAUCSL")]),
    "Crecimiento (encuestas Fed, media 3 meses)": ("Crecimiento (encuestas industriales de la Fed)", """
<p>Media de las encuestas manufactureras de la Fed de Filadelfia y la de Nueva York (Empire State), suavizada a 3 meses. Son índices de difusión: por encima de 0, más empresas ven mejora que empeoramiento.</p>
<p><b>Por qué estas.</b> Sustituyen al ISM manufacturero, que no está disponible en descarga gratuita.</p>""",
        [("Fed Filadelfia (FRED)", "https://fred.stlouisfed.org/data/GACDFSA066MSFRBPHI"),
         ("Fed Nueva York (FRED)", FRED + "GACDISA066MSFRBNY")]),
    "Estrés de crédito (Baa − bono 10 años)": ("Diferencial Baa − bono 10 años", """
<p>Rentabilidad extra que pagan los bonos corporativos con calificación Baa (el escalón más bajo del grado de inversión) sobre el bono del Tesoro a 10 años. Diario desde 1986.</p>
<p><b>Lectura.</b> Bajo = complacencia y crédito fácil; subiendo con rapidez = estrés. Es la serie de crédito que usan las reglas, porque permite comparar con 2002, 2008, 2020 y 2022.</p>""",
        [("Serie BAA10Y (FRED)", FRED + "BAA10Y")]),
    "Liquidez neta de la Fed (billones $)": ("Liquidez neta de la Fed", """
<p>Balance de la Fed menos la cuenta del Tesoro en la Fed menos el repo inverso. Aproxima la liquidez que la Fed deja en el sistema financiero.</p>
<p><b>Cuidado.</b> Es una heurística popular entre analistas desde 2020; la evidencia académica de su relación con la bolsa es escasa.</p>""",
        [("Balance WALCL (FRED)", FRED + "WALCL"), ("Cuenta del Tesoro WTREGEN (FRED)", FRED + "WTREGEN"),
         ("Repo inverso RRPONTSYD (FRED)", FRED + "RRPONTSYD")]),
    "Condiciones financieras (Chicago Fed)": ("Condiciones financieras (NFCI, Chicago Fed)", """
<p>Índice semanal que resume 105 medidas de riesgo, crédito y apalancamiento. Cero es la media histórica desde 1971.</p>
<p><b>Lectura.</b> Positivo = condiciones más tensas que la media; negativo = más holgadas que la media.</p>""",
        [("Chicago Fed: sobre el NFCI", "https://www.chicagofed.org/research/data/nfci/about")]),
    "Estrés financiero (St. Louis Fed)": ("Estrés financiero (St. Louis Fed)", """
<p>Índice semanal construido con 18 series de tipos de interés, diferenciales y otros indicadores. Cero = estrés normal.</p>
<p><b>Lectura.</b> Positivo = estrés por encima de la media; negativo = por debajo.</p>""",
        [("Serie STLFSI4 (FRED)", FRED + "STLFSI4")]),
    "Curva 10 años − 3 meses": ("Curva de tipos 10 años − 3 meses", """
<p>Diferencia entre el bono del Tesoro a 10 años y la letra a 3 meses.</p>
<p><b>Lectura.</b> Cuando se invierte (negativa) ha precedido a las recesiones de EE. UU. desde los años 50, con un año o dos de adelanto e imprecisión en el momento. Según la Fed de San Francisco, esta versión de la curva es la de mejor historial.</p>
""",
        [("Fed de San Francisco (2022)", "https://www.frbsf.org/research-and-insights/publications/economic-letter/current-recession-risk-according-to-yield-curve"),
         ("Serie T10Y3M (FRED)", FRED + "T10Y3M")]),
    "Tipo real 10 años (TIPS)": ("Tipo real a 10 años (TIPS)", """
<p>Rentabilidad del bono del Tesoro a 10 años protegido contra la inflación: el coste real del dinero a largo plazo.</p>
<p><b>Lectura.</b> Tipos reales altos y subiendo presionan a la baja la valoración de bolsa, oro y activos de larga duración; bajando, los favorecen.</p>""",
        [("Serie DFII10 (FRED)", FRED + "DFII10")]),
    "Inflación implícita 10 años": ("Inflación implícita a 10 años", """
<p>Diferencia entre el bono nominal a 10 años y el ligado a la inflación: la inflación media que descuenta el mercado para la próxima década.</p>
<p><b>Lectura.</b> Subiendo = el mercado teme más inflación (favorece activos reales); bajando = teme desinflación o recesión.</p>""",
        [("Serie T10YIE (FRED)", FRED + "T10YIE")]),
    "Dólar (índice amplio)": ("Dólar (índice amplio)", """
<p>Valor del dólar frente a una cesta amplia de divisas de sus socios comerciales.</p>
<p><b>Lectura.</b> Un dólar fuerte endurece las condiciones financieras globales y suele perjudicar a emergentes y materias primas. Para un inversor en euros, además, cambia el valor de todo lo que está en dólares.</p>""",
        [("Serie DTWEXBGS (FRED)", FRED + "DTWEXBGS")]),
    "Peticiones de subsidio (media 4 semanas)": ("Peticiones iniciales de subsidio por desempleo", """
<p>Nuevas solicitudes semanales de prestación por desempleo en EE. UU. (media de 4 semanas). Es de los datos de empleo más rápidos.</p>
<p><b>Lectura.</b> Subidas sostenidas suelen anticipar deterioro del mercado laboral. Ojo: el percentil compara con una historia desde 1967, con una población mucho menor.</p>""",
        [("Serie ICSA (FRED)", FRED + "ICSA")]),
    "Regla de Sahm": ("Regla de Sahm", """
<p>Compara la media de 3 meses del paro con su mínimo de los 12 meses anteriores. Si la diferencia llega a 0,50 puntos, marca el inicio de una recesión.</p>
<p><b>Lectura.</b> Es una señal de confirmación temprana, no de anticipación: salta cuando la recesión ya ha empezado.</p>""",
        [("Serie SAHMREALTIME (FRED)", FRED + "SAHMREALTIME")]),
    "Condiciones financieras FCI-G (Fed)": ("Condiciones financieras FCI-G (Reserva Federal)", """
<p>Índice de la Reserva Federal que traduce siete variables (tipos oficiales, bono a 10 años, hipotecas, bonos corporativos BBB, bolsa, vivienda y dólar) en su efecto sobre el PIB del año siguiente.</p>
<p><b>Lectura.</b> Positivo = viento en contra del crecimiento; negativo = viento a favor. Un valor de 1 equivale a restar (o sumar) un punto de crecimiento. Es mensual y se publica con retraso.</p>""",
        [("Reserva Federal (Ajello y otros, 2023)", "https://www.federalreserve.gov/econres/notes/feds-notes/a-new-index-to-measure-us-financial-conditions-20230630.html")]),
    "Cobre / oro": ("Ratio cobre / oro", """
<p>Precio del cobre (metal industrial, sensible al crecimiento) frente al oro (refugio).</p>
<p><b>Lectura.</b> Subiendo suele acompañar expectativas de más crecimiento y tipos más altos; bajando, de menos crecimiento o miedo.</p>
<p><b>Cuidado.</b> Es una heurística de mercado sin respaldo académico sólido. Aquí se mide con dos fondos cotizados (CPER y GLD), con historia desde 2011.</p>""", []),
    "Estructura del VIX (VIX/VIX3M)": ("Estructura del VIX (VIX/VIX3M)", """
<p>Volatilidad esperada a 1 mes frente a la de 3 meses.</p>
<p><b>Lectura.</b> Por debajo de 1 (contango) es lo normal. Por encima de 1 (inversión) indica miedo agudo a corto plazo. La vuelta por debajo de 0,95 tras una inversión cuenta como punto en la puerta.</p>""",
        [("VIX3M (Macroption)", "https://www.macroption.com/vix3m/")]),
    "Dispersión (DSPX)": ("Dispersión (Cboe DSPX)", """
<p>Mide cuánto se espera que difieran entre sí los valores del S&amp;P 500 en los próximos 30 días, a partir de opciones del índice y de opciones de sus valores. Lo publican Cboe y S&amp;P DJI desde 2023, con historia retrocalculada desde 2014.</p>
<p><b>Lectura.</b> Alta = cada valor va por su lado, más margen para la selección. Baja = todo se mueve junto.</p>""",
        [("Cboe: índice de dispersión", "https://www.cboe.com/us/indices/dispersion"),
         ("S&P DJI: preguntas frecuentes", "https://spglobal.com/spdji/en/education/article/faq-cboe-sp-500-dispersion-index")]),
    "Correlación implícita (COR3M)": ("Correlación implícita a 3 meses (Cboe COR3M)", """
<p>Correlación media esperada entre los 50 mayores valores del S&amp;P 500 en los próximos 3 meses, deducida de sus opciones. Cboe la describe como una medida del "comportamiento en manada".</p>
<p><b>Lectura.</b> Alta = la diversificación entre acciones aporta poco y manda el índice; baja = beneficios de diversificación altos y más margen para la selección.</p>""",
        [("Cboe: correlación implícita", "https://cboe.com/us/indices/implied/"),
         ("Cboe: documento técnico", "https://cdn.cboe.com/resources/indices/documents/Implied_Correlation-WhitePaper-v1.0.5.pdf")]),
    "Diferencial high yield (solo 3 años)": ("Diferencial high yield", """
<p>Prima que pagan los bonos corporativos de alto rendimiento sobre el Tesoro (índice de ICE BofA).</p>
<p><b>Lectura.</b> Es el termómetro de crédito más sensible al estrés.</p>
<p><b>Cuidado.</b> FRED solo ofrece sus 3 últimos años, así que su percentil no es histórico: se muestra como alarma complementaria y no entra en las reglas.</p>""",
        [("Serie BAMLH0A0HYM2 (FRED)", FRED + "BAMLH0A0HYM2")]),
    "Equiponderado / capitalización (RSP/SPY)": ("Equiponderado frente a capitalización (RSP/SPY)", """
<p>S&amp;P 500 con todas las empresas al mismo peso frente al S&amp;P 500 normal, ponderado por tamaño.</p>
<p><b>Lectura.</b> Subiendo = la subida se extiende a más empresas (amplitud sana). Bajando = mandan unas pocas grandes (concentración). Sustituye a las medidas de amplitud y concentración, que no tienen fuente gratuita.</p>""", []),

    # ---------------- OPCIONES ----------------
    "gamma": ("Gamma de los creadores de mercado (aproximada)", """
<p><b>Qué es.</b> Estimación de cómo tendrán que cubrirse los creadores de mercado de opciones del S&amp;P 500 cuando el índice se mueva.</p>
<ul><li><b>Positiva:</b> compran en las caídas y venden en las subidas → amortiguan los movimientos.</li>
<li><b>Negativa:</b> venden en las caídas y compran en las subidas → amplifican los movimientos.</li></ul>
<p><b>Cuidado.</b> Se basa en un supuesto simplificado y estándar (los creadores tienen las calls compradas y las puts vendidas). No predice la dirección del mercado, solo cómo de nervioso puede estar. Excluye las opciones que vencen el mismo día.</p>""",
        [("SpotGamma: qué es la exposición gamma", "https://spotgamma.com/gamma-exposure-explained/")]),
    "giro": ("Nivel de giro de la gamma", """
<p>Nivel del S&amp;P 500 en el que la gamma estimada cambia de signo.</p>
<p><b>Lectura.</b> Por encima del giro, el mercado tiende a estar amortiguado; por debajo, los movimientos tienden a amplificarse.</p>""", []),
    "muros": ("Muros de opciones", """
<p>Precios de ejercicio con más interés abierto en los próximos 70 días: el de puts por debajo del precio actual y el de calls por encima.</p>
<p><b>Lectura.</b> Suelen actuar como soporte (put) y resistencia (call) a corto plazo. Pierden fuerza tras los vencimientos.</p>""", []),
    "skew": ("SKEW (Cboe)", """
<p><b>Qué es.</b> Mide el precio de las opciones put muy fuera de dinero sobre el S&amp;P 500 frente a las opciones en dinero, a 30 días. Con rentabilidades "normales" valdría 100; sube cuanto más se paga por protegerse de caídas extremas. Suele moverse entre 100 y 150.</p>
<ul><li><b>Alto (más de 140):</b> los grandes inversores pagan mucho por cubrirse del riesgo de cola. Proteger la cartera con puts lejanas sale caro; venderlas paga más prima, a cambio de asumir ese riesgo.</li>
<li><b>No es una señal de caída inminente.</b> Mide el precio de la protección, no la probabilidad real de un desplome. Según algunos analistas, suele estar alto en mercados tranquilos, cuando cubrirse resulta barato en términos absolutos y los fondos compran protección de forma sistemática.</li></ul>
<p><b>Percentiles.</b> El histórico compara con toda su historia desde 1990; el anual, con el último año. El nivel del SKEW ha tendido a subir en las últimas décadas, así que el percentil anual es más útil para ver si está extremo ahora.</p>""",
        [("Cboe: metodología del SKEW", "https://cdn.cboe.com/resources/release_notes/2025/Consultation-Regarding-Proposed-Changes-to-the-Cboe-SKEW-Index.pdf"),
         ("Cboe: origen del skew", "https://www.cboe.com/insights/posts/dawn-of-a-new-era-brings-on-the-existence-of-skew")]),
    "putcall": ("Ratio put/call", """
<p><b>Qué es.</b> Volumen diario de opciones put (protección) dividido por el de calls (apuestas alcistas) en Cboe.</p>
<ul><li><b>Acciones:</b> refleja sobre todo al inversor particular; es el más usado como señal.</li>
<li><b>Índices y S&amp;P 500:</b> refleja más las coberturas institucionales; suele ser más alto que el de acciones.</li>
<li><b>Total:</b> la suma de ambos.</li></ul>
<p><b>Lectura.</b> Se usa como indicador contrario: extremos altos (mucho miedo) suelen coincidir con zonas de suelo y extremos bajos (complacencia), con zonas de techo. Mejor con la media de 10 días que con el dato de un día.</p>
<p><b>Cuidado.</b> No sirve para afinar fechas exactas.</p>""",
        [("StockCharts ChartSchool: ratio put/call", "https://chartschool.stockcharts.com/table-of-contents/market-indicators/put-call-ratio"),
         ("AAII: el ratio put/call", "https://www.aaii.com/journal/article/295384-the-put-call-ratio-viewing-market-sentiment-through-options-activity")]),
    "cot": ("Posicionamiento (CFTC)", """
<p><b>Qué es.</b> Informe semanal del regulador de futuros de EE. UU. con las posiciones de los grandes operadores (datos del martes, publicados el viernes).</p>
<ul><li><b>Gestoras:</b> fondos de inversión, pensiones y aseguradoras. Posicionamiento lento y estructural.</li>
<li><b>Fondos apalancados:</b> hedge funds y fondos de gestión tendencial (CTA). Más tácticos y rápidos.</li>
<li><b>Especuladores</b> (materias primas): gestores de dinero (fondos y CTA) que operan por cuenta de terceros.</li></ul>
<p><b>Lectura.</b> Neto = largos menos cortos, en % del interés abierto. Percentiles extremos (≥90 o ≤10 en 3 años) señalan posiciones muy cargadas, que pueden deshacerse con violencia si el mercado se gira.</p>""",
        [("CFTC: notas del informe financiero", "https://www.cftc.gov/idc/groups/public/@commitmentsoftraders/documents/file/tfmexplanatorynotes.pdf"),
         ("CFTC: notas del informe de materias primas", "https://www.cftc.gov/MarketReports/CommitmentsofTraders/DisaggregatedExplanatoryNotes/index.htm"),
         ("OFR: quiénes son los fondos apalancados", "https://financialresearch.gov/hedge-fund-monitor/categories/size/chart-94")]),

    # ---------------- CALENDARIO ----------------
    "blackout": ("Blackout de recompras (estimado)", """
<p><b>Qué es.</b> Periodo en el que la mayoría de empresas deja de recomprar acciones propias por iniciativa propia: desde unas 5 semanas antes de publicar resultados hasta unas 48 horas después. Según Goldman Sachs, las recompras son la mayor fuente de demanda de acciones en EE. UU.</p>
<p><b>Cuidado.</b> No hay dato oficial ni obligación legal: es una práctica de las empresas. Aquí se estima como una franja por trimestre.</p>""",
        [("Goldman Sachs (Kostin), vía Business Insider", "https://www.businessinsider.in/goldman-were-blaming-the-stock-market-sell-off-on-a-pullback-in-buybacks/articleshow/44889114.cms"),
         ("Goldman Sachs: las 48 horas posteriores, vía MarketWatch", "https://macdailynews.com/2015/03/24/goldman-sachs-buy-apple-during-buyback-blackout-period/"),
         ("Goldman Sachs: peso de las recompras, vía Bloomberg", "https://www.bloombergquint.com/markets/goldman-considers-a-world-without-buybacks-it-looks-ominous")]),
    "ciclo": ("Ciclo presidencial", """
<p>Rentabilidad media del S&amp;P 500 según el año del mandato presidencial de EE. UU. (datos de Shiller desde 1871).</p>
<p><b>Cuidado.</b> Es un patrón popular sin respaldo académico sólido para el año del mandato; la literatura académica más citada (Santa-Clara y Valkanov, 2003) estudia el partido del presidente, no el año del ciclo. Pocas observaciones: unas 38 por cada año del ciclo.</p>""",
        [("Santa-Clara y Valkanov (2003), Journal of Finance", "https://doi.org/10.1111/1540-6261.00590")]),
    "estacionalidad": ("Estacionalidad mensual", """
<p>Rentabilidad media y mediana del S&amp;P 500 en cada mes del año, y porcentaje de años en que ese mes fue positivo.</p>
<p><b>Evidencia.</b> El efecto más estudiado es "Sell in May" (efecto Halloween): Bouman y Jacobsen (2002) hallaron mejores rentabilidades de noviembre a abril en 36 de 37 mercados. En EE. UU., otros autores atribuyen el resultado a dos meses extremos (octubre de 1987 y agosto de 1998).</p>
<p><b>Cuidado.</b> Efectos débiles e inestables; no deben pesar más que el régimen.</p>""",
        [("Bouman y Jacobsen (2002), American Economic Review", "https://doi.org/10.1257/000282802762024683"),
         ("Crítica en Econ Journal Watch", "https://econjwatch.org/articles/stock-market-efficiency-withstands-another-challenge-solving-the-sell-in-may-buy-after-halloween-puzzle")]),
    "cambio_mes": ("Efecto cambio de mes", """
<p>Las bolsas tienden a subir más en el último día hábil del mes y los tres primeros del siguiente (Lakonishok y Smidt, 1988; Ariel, 1987). Una de las explicaciones propuestas son los flujos de dinero periódicos de fin de mes (nóminas, pensiones, fondos).</p>
<p><b>Cuidado.</b> Útil, como mucho, para afinar el día de una compra ya decidida.</p>""",
        [("McConnell y Xu: el efecto persiste", "https://docs.lib.purdue.edu/ciberwp/43"),
         ("Ariel (1987), Journal of Financial Economics", "https://doi.org/10.1016/0304-405X(87)90066-3")]),
    "Opciones": ("Vencimientos de opciones", """
<ul><li><b>Mensual:</b> tercer viernes de cada mes. Al vencer, se liberan coberturas y cambia la gamma.</li>
<li><b>Trimestral:</b> tercer viernes de marzo, junio, septiembre y diciembre. Vencen a la vez opciones y futuros sobre índices y acciones, y además se rebalancea el S&amp;P 500. Mucho volumen al cierre.</li>
<li><b>VIX:</b> miércoles, 30 días antes del vencimiento mensual del mes siguiente.</li></ul>""", []),
    "Cierres": ("Cierres de trimestre y de año", """
<p>Las gestoras y los fondos de pensiones rebalancean sus carteras y "maquillan" posiciones antes de publicar resultados. Puede haber flujos grandes los últimos días, sobre todo tras trimestres con mucha diferencia entre bolsa y bonos.</p>""", []),
    "Recompras": ("Recompras", """<p>Inicio y fin estimados del blackout de recompras de cada trimestre. Ver la tarjeta "Blackout de recompras".</p>""", []),
    "Índices": ("Reconstitución de índices Russell", """
<p>FTSE Russell recompone sus índices de EE. UU. (Russell 1000, 2000…). Desde 2026 lo hace dos veces al año, en junio y diciembre. Genera un volumen enorme al cierre del día efectivo, sobre todo en small caps. El día de referencia fija qué empresas entran y salen.</p>""",
        [("FTSE Russell: reconstitución de diciembre de 2026", "https://www.lseg.com/en/media-centre/press-releases/ftse-russell/2026/ftse-russell-begins-december-2026-semi-annual-russell-us-indexes-reconstitution")]),
    "Fed": ("Reuniones de la Fed (FOMC)", """
<p>Decisión de tipos de interés y, en las reuniones trimestrales, nuevas previsiones. El día de la decisión aumenta la volatilidad de bonos, dólar y bolsa.</p>""",
        [("Reserva Federal: calendario", "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")]),
    "Datos": ("IPC de EE. UU.", """
<p>Publicación mensual de la inflación (8:30 de Nueva York; normalmente 14:30 en España). Es uno de los datos que más mueve los mercados mientras la inflación sea el centro de la política de la Fed.</p>""",
        [("BLS: calendario del IPC", "https://www.bls.gov/schedule/news_release/cpi.htm")]),
    "Fiscal": ("Fechas fiscales", """
<ul><li><b>31 de marzo:</b> cierre del año fiscal en Japón (repatriación y rebalanceo de inversores japoneses).</li>
<li><b>15 de abril:</b> pago de impuestos en EE. UU. (salida de liquidez).</li>
<li><b>31 de octubre:</b> fin de ejercicio fiscal de muchos fondos de inversión estadounidenses (ventas para materializar pérdidas).</li>
<li><b>Diciembre:</b> ventas de particulares para compensar pérdidas en impuestos; Roll (1983) lo relacionó con el rebote de enero.</li></ul>""", []),
}


# ----------------------------------------------------------------------------
# CONFIGURACIÓN
# ----------------------------------------------------------------------------

CARPETA_WEB = "docs"
CARPETA_DATOS = "datos"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

AHORA = pd.Timestamp.now(tz="UTC").tz_convert(None)
HOY = AHORA.normalize()
# Datos completos hasta ayer; última semana cerrada = último viernes <= ayer
_REF = HOY - pd.Timedelta(days=1)
ULTIMO_VIERNES = _REF - pd.Timedelta(days=(_REF.weekday() - 4) % 7)

MOTORES = [
    ("ACWI", "RV global"), ("SPY", "RV EE. UU."), ("RSP", "RV EE. UU. equiponderada"),
    ("VGK", "RV Europa"), ("EEM", "RV emergentes"), ("QQQ", "Nasdaq"),
    ("IWM", "Small caps EE. UU."), ("VTV", "Value"), ("VUG", "Growth"),
    ("MTUM", "Momentum"), ("QUAL", "Calidad"), ("BTAL", "Anti-beta"),
    ("TLT", "Bonos largos"), ("IEF", "Bonos medios"), ("TIP", "Bonos ligados a inflación"),
    ("HYG", "High yield"), ("DBC", "Materias primas"), ("XLE", "Energía"),
    ("GLD", "Oro"), ("URA", "Uranio"), ("UUP", "Dólar"), ("FXE", "Euro"),
    ("DBMF", "Managed futures"), ("VIXY", "Volatilidad"),
]
REFERENCIA = "BIL"
OTROS_TIINGO = ["CPER", "URTH", "IEMG"]

# Índices para las condiciones de indexación: (ticker, nombre, ¿EE. UU.?)
INDICES_COND = [("URTH", "MSCI World", False), ("SPY", "S&P 500", True),
                ("RSP", "S&P 500 equiponderado", True), ("VGK", "Europa", False),
                ("IEMG", "Emergentes (MSCI EM IMI)", False)]

GRUPO = {
    "ACWI": "Renta variable", "SPY": "Renta variable", "RSP": "Renta variable", "VGK": "Renta variable",
    "EEM": "Renta variable", "QQQ": "Renta variable", "IWM": "Renta variable",
    "VTV": "Factores", "VUG": "Factores", "MTUM": "Factores", "QUAL": "Factores",
    "TLT": "Renta fija", "IEF": "Renta fija", "TIP": "Renta fija", "HYG": "Crédito",
    "DBC": "Materias primas", "XLE": "Materias primas", "GLD": "Materias primas", "URA": "Materias primas",
    "UUP": "Divisas", "FXE": "Divisas", "BTAL": "Alternativos", "DBMF": "Alternativos", "VIXY": "Alternativos",
}
COLOR_GRUPO = {"Renta variable": "#2f6fdb", "Factores": "#8e44ad", "Renta fija": "#1f9d55",
               "Crédito": "#a0672a", "Materias primas": "#e67e22", "Divisas": "#c0392b",
               "Alternativos": "#16a085"}
DESC = {
    "ACWI": "iShares MSCI ACWI: bolsa mundial, países desarrollados y emergentes.",
    "SPY": "SPDR S&P 500: las 500 mayores empresas de EE. UU., ponderadas por tamaño.",
    "RSP": "Invesco S&P 500 Equal Weight: las mismas 500 empresas, todas con el mismo peso.",
    "VGK": "Vanguard FTSE Europe: bolsa de Europa desarrollada.",
    "EEM": "iShares MSCI Emerging Markets: bolsa de países emergentes.",
    "QQQ": "Invesco QQQ: Nasdaq-100, las 100 mayores empresas no financieras del Nasdaq (sesgo tecnológico).",
    "IWM": "iShares Russell 2000: empresas pequeñas de EE. UU.",
    "VTV": "Vanguard Value: empresas de EE. UU. baratas por valoración.",
    "VUG": "Vanguard Growth: empresas de EE. UU. de alto crecimiento.",
    "MTUM": "iShares MSCI USA Momentum: acciones de EE. UU. con mejor comportamiento reciente.",
    "QUAL": "iShares MSCI USA Quality: empresas de EE. UU. rentables, poco endeudadas y estables.",
    "BTAL": "Fondo anti-beta de AGF: compra acciones de beta baja y vende las de beta alta en igual importe. Aproximación a market neutral.",
    "TLT": "iShares 20+ Year Treasury: bonos del Tesoro de EE. UU. a más de 20 años.",
    "IEF": "iShares 7-10 Year Treasury: bonos del Tesoro de EE. UU. a 7-10 años.",
    "TIP": "iShares TIPS: bonos del Tesoro de EE. UU. ligados a la inflación.",
    "HYG": "iShares iBoxx High Yield: bonos corporativos de alto rendimiento en dólares.",
    "DBC": "Invesco DB Commodity: cesta diversificada de futuros de materias primas.",
    "XLE": "Energy Select Sector SPDR: sector energía del S&P 500.",
    "GLD": "SPDR Gold Shares: oro físico.",
    "URA": "Global X Uranium: empresas de minería y combustible de uranio.",
    "UUP": "Invesco DB US Dollar Bullish: dólar frente a una cesta de divisas desarrolladas.",
    "FXE": "Invesco CurrencyShares Euro: euro frente al dólar.",
    "DBMF": "iMGP DBi Managed Futures: réplica de los fondos de gestión tendencial.",
    "VIXY": "ProShares VIX Short-Term Futures: futuros del VIX a corto plazo. Sube en los picos de miedo y pierde valor con el tiempo en mercados tranquilos.",
    "BIL": "SPDR 1-3 Month T-Bill: letras del Tesoro de EE. UU., la referencia de liquidez.",
    "URTH": "iShares MSCI World: bolsa de países desarrollados.",
    "IEMG": "iShares Core MSCI Emerging Markets: emergentes de empresas grandes, medianas y pequeñas (índice IMI).",
    "CPER": "United States Copper Index Fund: futuros de cobre.",
}


def tk(t):
    return f'<span class="tk" data-tip="{html.escape(t + " · " + DESC.get(t, ""))}">{html.escape(t)}</span>'


FRED_SERIES = {
    "T10YIE": "Inflación implícita 10 años",
    "CPIAUCSL": "IPC EE. UU.",
    "DFII10": "Tipo real 10 años (TIPS)",
    "ICSA": "Peticiones de subsidio",
    "SAHMREALTIME": "Regla de Sahm",
    "GACDFSA066MSFRBPHI": "Encuesta Fed Filadelfia",
    "GACDISA066MSFRBNY": "Encuesta Fed Nueva York",
    "BAMLH0A0HYM2": "Diferencial high yield (3 años)",
    "BAA10Y": "Diferencial Baa − bono 10 años",
    "T10Y3M": "Curva 10 años − 3 meses",
    "NFCI": "Condiciones financieras (Chicago Fed)",
    "STLFSI4": "Estrés financiero (St. Louis Fed)",
    "WALCL": "Balance de la Fed",
    "WTREGEN": "Cuenta del Tesoro",
    "RRPONTSYD": "Repo inverso",
    "DTWEXBGS": "Dólar (índice amplio)",
}

CBOE_INDICES = ["VIX", "VIX3M", "COR3M", "DSPX", "SKEW"]

# Calendarios oficiales (revisar cada año)
FOMC = [
    ("2026-12-08", "2026-12-09"),
    ("2027-01-26", "2027-01-27"), ("2027-03-16", "2027-03-17"),
    ("2027-04-27", "2027-04-28"), ("2027-06-08", "2027-06-09"),
    ("2027-07-27", "2027-07-28"), ("2027-09-14", "2027-09-15"),
    ("2027-10-26", "2027-10-27"), ("2027-11-16", "2027-11-17"),
]
IPC_EEUU = [
    "2026-12-10", "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13",
    "2027-05-12", "2027-06-10", "2027-07-14", "2027-08-11", "2027-09-14",
    "2027-10-14", "2027-11-10", "2027-12-10",
]

ERRORES = []   # (fuente, mensaje)
FECHAS = {}    # fuente -> última fecha disponible


# ----------------------------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------------------------

INICIO = time.time()
LIMITE_DESCARGAS = INICIO + 10 * 60   # nunca más de 10 minutos descargando
SERVIDOR_FALLOS = {}                   # servidor -> fallos seguidos
SERVIDOR_CAIDO = set()                 # servidores que no se vuelven a intentar hoy


class NoEncontrado(Exception):
    pass


def get(url, params=None, intentos=2, espera=2, timeout=(10, 30)):
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    if host in SERVIDOR_CAIDO:
        raise RuntimeError(f"{host} no responde hoy (omitido)")
    ultimo = ""
    for i in range(intentos):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200:
                SERVIDOR_FALLOS[host] = 0
                return r
            if r.status_code == 404:
                raise NoEncontrado("HTTP 404")
            if r.status_code == 429:          # límite de peticiones: no insistir
                SERVIDOR_CAIDO.add(host)
                raise RuntimeError("HTTP 429 (límite de peticiones)")
            ultimo = f"HTTP {r.status_code}"
            if 400 <= r.status_code < 500:
                raise RuntimeError(ultimo)
        except (NoEncontrado, RuntimeError):
            raise
        except Exception as e:  # noqa  (tiempo agotado, conexión cortada...)
            ultimo = str(e)[:120]
        if i < intentos - 1:
            time.sleep(espera)
    SERVIDOR_FALLOS[host] = SERVIDOR_FALLOS.get(host, 0) + 1
    if SERVIDOR_FALLOS[host] >= 2:
        SERVIDOR_CAIDO.add(host)
    raise RuntimeError(ultimo)


def seguro(nombre, fn, *a, **k):
    if time.time() > LIMITE_DESCARGAS:
        ERRORES.append((nombre, "sin tiempo"))
        print(f"[{time.time() - INICIO:5.0f}s] {nombre}: SALTADO (sin tiempo)", flush=True)
        return None
    t0 = time.time()
    try:
        r = fn(*a, **k)
        print(f"[{time.time() - INICIO:5.0f}s] {nombre}: ok ({time.time() - t0:.0f}s)", flush=True)
        return r
    except Exception as e:  # noqa
        ERRORES.append((nombre, str(e)[:180]))
        print(f"[{time.time() - INICIO:5.0f}s] {nombre}: FALLO ({time.time() - t0:.0f}s) {str(e)[:120]}", flush=True)
        return None


def semanal(s):
    if s is None or len(s) == 0:
        return None
    w = s.resample("W-FRI").last().dropna()
    return w[w.index <= ULTIMO_VIERNES]


def mensual(s):
    if s is None or len(s) == 0:
        return None
    return s.resample("MS").last().dropna()


def pct(s, valor=None):
    s = s.dropna()
    if len(s) == 0:
        return np.nan
    v = s.iloc[-1] if valor is None else valor
    return float((s <= v).mean() * 100)


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def fmt(x, dec=2, suf=""):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"{x:,.{dec}f}{suf}".replace(",", "X").replace(".", ",").replace("X", ".")


def e(t):
    return html.escape(str(t))


def i(k):
    return f' <button class="info" data-k="{html.escape(k)}" title="¿Qué es?">ⓘ</button>' if k in INFO else ""


def info_json():
    datos = {}
    for k, (tit, cuerpo, fuentes) in INFO.items():
        f = ""
        if fuentes:
            f = '<p class="fuentes-info"><b>Fuentes:</b> ' + " · ".join(
                f'<a href="{html.escape(u)}" target="_blank" rel="noopener">{html.escape(n)}</a>' for n, u in fuentes) + "</p>"
        datos[k] = {"t": tit, "h": cuerpo.strip() + f}
    return json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")


def antiguedad_ok(fecha, frecuencia):
    if fecha is None:
        return False
    limite = {"d": 7, "s": 16, "m": 75}[frecuencia]
    return (HOY - pd.Timestamp(fecha)).days <= limite


def registrar(nombre, s):
    if s is not None and len(s):
        FECHAS[nombre] = s.index[-1]


# ----------------------------------------------------------------------------
# DESCARGAS
# ----------------------------------------------------------------------------

def fred(sid):
    clave = os.environ.get("FRED_API_KEY", "").strip()
    if not clave:
        raise RuntimeError("falta la clave de FRED")
    r = get("https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": sid, "api_key": clave, "file_type": "json"})
    obs = r.json().get("observations", [])
    s = pd.Series(pd.to_numeric([o.get("value") for o in obs], errors="coerce"),
                  index=pd.to_datetime([o.get("date") for o in obs], errors="coerce"))
    s = s[s.index.notna()].dropna().sort_index()
    if len(s) == 0:
        raise RuntimeError("serie vacía")
    return s.rename(sid)


def cboe_indice(nombre):
    url = f"https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{nombre}_History.csv"
    df = pd.read_csv(io.StringIO(get(url).text))
    s = pd.Series(pd.to_numeric(df.iloc[:, -1], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0], format="%m/%d/%Y", errors="coerce"))
    s = s[s.index.notna()].dropna().sort_index()
    if len(s) == 0:
        raise RuntimeError("serie vacía")
    return s.rename(nombre)


def tiingo(ticker, frecuencia="weekly"):
    clave = os.environ.get("TIINGO_API_KEY", "").strip()
    if not clave:
        raise RuntimeError("falta la clave de Tiingo")
    r = get(f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
            params={"startDate": "1990-01-01", "resampleFreq": frecuencia, "token": clave}, intentos=1)
    d = r.json()
    if not isinstance(d, list) or not d:
        raise RuntimeError(str(d)[:120])
    df = pd.DataFrame(d)
    idx = pd.to_datetime(df["date"], utc=True).dt.tz_convert(None).dt.normalize()
    s = pd.Series(df["adjClose"].astype(float).values, index=idx).sort_index()
    return s.rename(ticker)


def _leer_excel(contenido):
    try:
        import python_calamine  # noqa
    except ImportError:
        import subprocess
        import sys
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "python-calamine"], check=False)
    try:
        return pd.read_excel(io.BytesIO(contenido), sheet_name="Data", header=None, engine="calamine")
    except Exception:  # noqa
        return pd.read_excel(io.BytesIO(contenido), sheet_name="Data", header=None)


def shiller():
    pagina = get("https://shillerdata.com/").text
    enlaces = re.findall(r'href="([^"]*ie_data\.xls[^"]*)"', pagina)
    if not enlaces:
        raise RuntimeError("no se encuentra el archivo")
    url = html.unescape(enlaces[0])
    if url.startswith("//"):
        url = "https:" + url
    raw = _leer_excel(get(url, timeout=(10, 60)).content)

    col0 = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
    arranque = col0[(col0 > 1870) & (col0 < 1872)]
    if arranque.empty:
        raise RuntimeError("formato no reconocido (fechas)")
    inicio = raw.index.get_loc(arranque.index[0])
    cab = raw.iloc[:inicio]
    textos = [" ".join(str(v) for v in cab.iloc[:, k] if pd.notna(v)).upper() for k in range(raw.shape[1])]
    datos = raw.iloc[inicio:]
    fecha = pd.to_numeric(datos.iloc[:, 0], errors="coerce")
    datos = datos[fecha.notna()]
    fecha = fecha[fecha.notna()]

    def col(k):
        return pd.to_numeric(datos.iloc[:, k], errors="coerce").reset_index(drop=True)

    def ultimo(k):
        v = col(k).dropna()
        return v.iloc[-1] if len(v) else np.nan

    def buscar(cond, rango):
        for k, t in enumerate(textos):
            if cond(t) and rango[0] <= ultimo(k) <= rango[1]:
                return k
        return None

    k_cape = buscar(lambda t: "CAPE" in t and "TR" not in t and "YIELD" not in t and "EXCESS" not in t, (5, 80))
    if k_cape is None:
        raise RuntimeError("formato no reconocido (CAPE)")
    k_ecy = buscar(lambda t: "YIELD" in t and ("CAPE" in t or "EXCESS" in t), (-0.2, 0.3))
    k_gs10 = buscar(lambda t: "GS10" in t, (0, 20))
    k_cpi = buscar(lambda t: "CPI" in t, (50, 1000))
    k_gs10 = 6 if k_gs10 is None else k_gs10
    k_cpi = 4 if k_cpi is None else k_cpi

    anio = np.floor(fecha.values).astype(int)
    mes = np.clip(np.round((fecha.values - anio) * 100).astype(int), 1, 12)
    idx = pd.to_datetime(pd.DataFrame({"year": anio, "month": mes, "day": 1}))
    out = pd.DataFrame({"P": col(1).values, "CPI": col(k_cpi).values,
                        "GS10": col(k_gs10).values, "CAPE": col(k_cape).values}, index=idx.values)
    infl10 = (out["CPI"] / out["CPI"].shift(120)) ** 0.1 - 1
    out["ECY"] = 100 / out["CAPE"] - (out["GS10"] - 100 * infl10)
    if k_ecy is not None:                      # cifra publicada por Shiller, si existe
        out["ECY"] = col(k_ecy).values * 100
    out = out[~out.index.duplicated(keep="last")].sort_index()
    ult = out["ECY"].dropna()
    if ult.empty or not (-5 <= ult.iloc[-1] <= 15):
        raise RuntimeError("valor de ECY fuera de rango")
    return out


def fcig():
    url = "https://www.federalreserve.gov/econres/notes/feds-notes/fci_g_public_monthly_3yr.csv"
    df = pd.read_csv(io.StringIO(get(url).text))
    cols = [c for c in df.columns[1:] if "fci" in str(c).lower()]
    col = cols[0] if cols else df.columns[1]
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0], errors="coerce"))
    s = s[s.index.notna()].dropna().sort_index()
    if len(s) == 0:
        raise RuntimeError("serie vacía")
    return s.rename("FCIG")


def cot(dataset, codigo, largo, corto):
    r = get(f"https://publicreporting.cftc.gov/resource/{dataset}.json",
            params={"cftc_contract_market_code": codigo,
                    "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 1500})
    df = pd.DataFrame(r.json())
    if df.empty:
        raise RuntimeError("sin datos")
    idx = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    neto = (pd.to_numeric(df[largo]) - pd.to_numeric(df[corto])) / pd.to_numeric(df["open_interest_all"]) * 100
    return pd.Series(neto.values, index=idx).sort_index()


# ----------------------------------------------------------------------------
# HISTÓRICOS PROPIOS (put/call y gamma)
# ----------------------------------------------------------------------------

def leer_hist(nombre):
    ruta = os.path.join(CARPETA_DATOS, nombre)
    if os.path.exists(ruta):
        df = pd.read_csv(ruta, parse_dates=["fecha"]).set_index("fecha").sort_index()
        return df[~df.index.duplicated(keep="last")]
    return pd.DataFrame()


def guardar_hist(nombre, df):
    os.makedirs(CARPETA_DATOS, exist_ok=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index.name = "fecha"
    df.to_csv(os.path.join(CARPETA_DATOS, nombre))


PC_CLAVES = {
    "total": "TOTAL PUT/CALL RATIO",
    "acciones": "EQUITY PUT/CALL RATIO",
    "indices": "INDEX PUT/CALL RATIO",
    "spx": "SPX",
}


def putcall_dia(fecha):
    url = f"https://cdn.cboe.com/data/us/options/market_statistics/daily/{fecha:%Y-%m-%d}_daily_options"
    try:
        r = get(url, intentos=1, timeout=(10, 15))
    except NoEncontrado:
        return None
    d = r.json()
    ratios = d.get("ratios", []) if isinstance(d, dict) else []
    fila = {}
    for it in ratios:
        nombre = str(it.get("name", "")).upper()
        try:
            valor = float(it.get("value"))
        except Exception:  # noqa
            continue
        if nombre == "TOTAL PUT/CALL RATIO":
            fila["total"] = valor
        elif nombre == "EQUITY PUT/CALL RATIO":
            fila["acciones"] = valor
        elif nombre == "INDEX PUT/CALL RATIO":
            fila["indices"] = valor
        elif nombre.startswith("SPX + SPXW") and "PUT/CALL" in nombre:
            fila["spx"] = valor
    return fila or None


def actualizar_putcall():
    hist = leer_hist("putcall.csv")
    dias = pd.bdate_range(HOY - pd.Timedelta(days=400), HOY - pd.Timedelta(days=1))
    pendientes = [d for d in dias if d not in hist.index]
    pendientes = pendientes[-300:]
    nuevas = {}
    fallos = 0
    limite = min(time.time() + 180, LIMITE_DESCARGAS)   # como mucho 3 minutos por ejecución
    for d in reversed(pendientes):
        if time.time() > limite:
            break
        try:
            fila = putcall_dia(d)
        except Exception:  # noqa  servidor caído: dejarlo para mañana
            break
        if fila:
            nuevas[d] = fila
            fallos = 0
        else:
            fallos += 1
        if fallos >= 15:   # festivos o fin del archivo disponible
            break
        time.sleep(0.3)
    if nuevas:
        hist = pd.concat([hist, pd.DataFrame.from_dict(nuevas, orient="index")])
        guardar_hist("putcall.csv", hist)
    if hist.empty:
        raise RuntimeError("sin datos")
    return hist.sort_index()


def _gamma_bs(S, K, T, iv):
    sq = np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * iv ** 2 * T) / (iv * sq)
    return np.exp(-0.5 * d1 ** 2) / np.sqrt(2 * np.pi) / (S * iv * sq)


def gamma_spx(spy_ultimo=None):
    d = get("https://cdn-api.cboe.com/api/global/delayed_quotes/options/_SPX.json", timeout=120).json()
    datos = d["data"]
    op = pd.DataFrame(datos["options"])
    m = op["option"].str.extract(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")
    op["vto"] = pd.to_datetime(m[1], format="%y%m%d", errors="coerce") + pd.Timedelta(hours=21)
    op["tipo"] = m[2]
    op["K"] = pd.to_numeric(m[3], errors="coerce") / 1000
    for c in ["iv", "open_interest", "bid", "ask"]:
        op[c] = pd.to_numeric(op[c], errors="coerce")
    op = op.dropna(subset=["vto", "tipo", "K"])
    op["T"] = (op["vto"] - AHORA).dt.total_seconds() / (365 * 86400)
    op = op[op["T"] > 0]

    spot = None
    for clave in ("current_price", "close", "last_trade_price", "price"):
        v = datos.get(clave) if isinstance(datos, dict) else None
        try:
            if v and float(v) > 0:
                spot = float(v)
                break
        except Exception:  # noqa
            pass
    if spot is None:   # paridad put-call en el vencimiento más cercano
        cerc = op[op["vto"] == op["vto"].min()].copy()
        cerc["mid"] = (cerc["bid"] + cerc["ask"]) / 2
        tabla = cerc.pivot_table(index="K", columns="tipo", values="mid").dropna()
        tabla = tabla[(tabla["C"] > 0) & (tabla["P"] > 0)]
        k = (tabla["C"] - tabla["P"]).abs().idxmin()
        spot = float(k + tabla.loc[k, "C"] - tabla.loc[k, "P"])

    if spy_ultimo:
        if abs(spot / (spy_ultimo * 10) - 1) > 0.04:
            raise RuntimeError("precio del S&P 500 incoherente")
    op = op[(op["open_interest"] > 0) & (op["iv"] > 0.01) & (op["T"] > 1 / 365) & (op["T"] < 1.5)]
    signo = np.where(op["tipo"].values == "C", 1.0, -1.0)
    K = op["K"].values
    T = op["T"].values
    iv = op["iv"].values
    oi = op["open_interest"].values

    def gex(S):
        g = _gamma_bs(S, K, T, iv)
        return float(np.nansum(signo * g * oi * 100 * S * S * 0.01))

    actual = gex(spot)
    rejilla = np.linspace(spot * 0.85, spot * 1.15, 121)
    perfil = np.array([gex(s) for s in rejilla])
    giro = None
    cambios = np.where(np.diff(np.sign(perfil)) != 0)[0]
    if len(cambios):
        mejores = sorted(cambios, key=lambda i: abs(rejilla[i] - spot))
        i = mejores[0]
        x0, x1, y0, y1 = rejilla[i], rejilla[i + 1], perfil[i], perfil[i + 1]
        giro = float(x0 - y0 * (x1 - x0) / (y1 - y0))

    cerca = op[op["T"] < 70 / 365]
    calls = cerca[cerca["tipo"] == "C"].groupby("K")["open_interest"].sum()
    puts = cerca[cerca["tipo"] == "P"].groupby("K")["open_interest"].sum()
    muro_call = float(calls[calls.index >= spot].idxmax()) if len(calls[calls.index >= spot]) else None
    muro_put = float(puts[puts.index <= spot].idxmax()) if len(puts[puts.index <= spot]) else None

    res = {"spot": spot, "gex_mm": actual / 1e9, "giro": giro,
           "muro_call": muro_call, "muro_put": muro_put,
           "fecha": str(d.get("timestamp", AHORA))}
    hist = leer_hist("gamma.csv")
    fila = pd.DataFrame({"gex_mm": [res["gex_mm"]],
                         "dist_giro_pct": [((spot / giro - 1) * 100) if giro else np.nan]},
                        index=[HOY])
    hist = pd.concat([hist, fila])
    guardar_hist("gamma.csv", hist)
    res["hist"] = hist.sort_index()
    return res


# ----------------------------------------------------------------------------
# CÁLCULOS DEL DIAGNOSTICADOR
# ----------------------------------------------------------------------------

def rrg(precio_w, ref_w):
    df = pd.concat([precio_w, ref_w], axis=1, join="inner").dropna()
    rs = df.iloc[:, 0] / df.iloc[:, 1]
    X = 100 * rs / rs.rolling(26).mean()
    Y = 100 * X / X.shift(4)
    return rs, X.dropna(), Y.dropna()


def fase_motor(precio_w, rs, X, Y):
    x, y = X.iloc[-1], Y.iloc[-1]
    dy = Y.diff().iloc[-3:]
    sube3 = bool((dy > 0).all())
    baja3 = bool((dy < 0).all())
    r = rsi(precio_w).iloc[-1]
    dist = (precio_w / precio_w.rolling(40).mean() - 1).dropna()
    pd_ = pct(dist)
    prs = pct(rs)
    alc = int(r > 70) + int(pd_ >= 90) + int(prs > 90)
    baj = int(r < 30) + int(pd_ <= 10) + int(prs < 10)
    if x >= 100 and y < 100:
        f = "GIRO"
    elif x >= 100:
        f = "MADUREZ" if (baja3 or alc >= 2) else "ACELERACIÓN"
    elif y >= 100:
        f = "ARRANQUE"
    else:
        f = "SUELO" if (sube3 or baj >= 2) else "REZAGADO"
    return f, {"rsi": r, "alc": alc, "baj": baj}


CLAVE_COND = {"Precio cerca de la media de 200 semanas": "c_ma200",
              "Crédito girando a la baja desde su pico": "c_credito",
              "VIX vuelve a contango tras inversión": "c_vix",
              "RSI semanal en sobreventa": "c_rsi"}
CLAVE_BLOQUE = {"A · Régimen": "b_A", "B · Estrés": "b_B", "C · Liquidez": "b_C",
                "E+F+G · Valoración, técnico y sentimiento": "b_EFG"}

ACCION = {
    "SUELO": "Vigilar", "ARRANQUE": "Construir poco a poco", "ACELERACIÓN": "Mantener",
    "MADUREZ": "Empezar a salir", "GIRO": "Salir", "REZAGADO": "No tocar",
}
COLOR_FASE = {
    "SUELO": "#6b7a90", "ARRANQUE": "#2f6fdb", "ACELERACIÓN": "#1f9d55",
    "MADUREZ": "#d9a400", "GIRO": "#e0661b", "REZAGADO": "#c0392b",
}


def fase_macro(nivel_pct, impulso, frenando3):
    if nivel_pct >= 50 and impulso > 0:
        return "MADUREZ" if (nivel_pct > 80 and frenando3) else "ACELERACIÓN"
    if nivel_pct >= 50:
        return "GIRO"
    if impulso > 0:
        return "ARRANQUE"
    return "SUELO" if (nivel_pct < 20 and frenando3) else "REZAGADO"


def variable_macro(nombre, s, frecuencia, periodos, modo="dif"):
    """s: serie ya en su frecuencia (semanal o mensual)."""
    s = s.dropna()
    if len(s) < periodos + 10:
        return None
    imp = (s / s.shift(periodos) - 1) * 100 if modo == "pct" else s - s.shift(periodos)
    imp = imp.dropna()
    frenando3 = bool((imp.abs().diff().iloc[-3:] < 0).all())
    niv = pct(s)
    ultimo = imp.iloc[-1]
    fase = fase_macro(niv, ultimo, frenando3)
    desde = s.index[-1] - pd.Timedelta(weeks=24)
    ventana = imp[imp.index >= desde].index
    rastro_x = [pct(s, s.loc[d]) for d in ventana]
    rastro_y = [pct(imp, imp.loc[d]) for d in ventana]
    return {"nombre": nombre, "valor": s.iloc[-1], "fecha": s.index[-1], "nivel": niv,
            "impulso": ultimo, "fase": fase, "frecuencia": frecuencia,
            "rx": rastro_x, "ry": rastro_y, "modo": modo, "periodos": periodos}


V_MEMORIA = 13


def puerta_comun(baa_w, ratio_vix_w):
    """Condiciones comunes a todos los índices: crédito, VIX y veto."""
    res = {"credito": None, "vix": None, "veto": False, "baa_13s": None, "ratio_vix": None}
    V = V_MEMORIA
    if baa_w is not None and len(baa_w) > 120:
        b = baa_w.values
        sma4 = baa_w.rolling(4).mean().values
        sma13 = baa_w.rolling(13).mean().values
        n = len(b)
        hits = []
        for k in range(n - V, n):
            ini = max(0, k - 25)
            j = ini + int(np.argmax(b[ini:k + 1]))
            M = b[j]
            previo = b[max(0, j - 52):j]
            if len(previo) == 0:
                hits.append(False)
                continue
            hits.append(bool(M >= 1.4 * previo.min() and b[k] <= 0.85 * M and sma4[k] < sma13[k]))
        res["credito"] = any(hits)
        cond = (baa_w / baa_w.shift(13) - 1 >= 0.30) & ((baa_w - baa_w.shift(4)) > (baa_w.shift(4) - baa_w.shift(8)))
        res["veto"] = bool(cond.iloc[-4:].any())
        res["baa_13s"] = (baa_w.iloc[-1] / baa_w.iloc[-14] - 1) * 100
    if ratio_vix_w is not None and len(ratio_vix_w) > 40:
        r = ratio_vix_w.values
        n = len(r)
        hits = []
        for k in range(n - V, n):
            if k < 15:
                continue
            hits.append(bool(r[k - 1] < 0.95 and r[k] < 0.95 and (r[max(0, k - 14):k - 1] > 1).any()))
        res["vix"] = any(hits)
        res["ratio_vix"] = float(r[-1])
    return res


def puerta_indice(px_w, comun):
    """Puntos de la puerta para un índice: su media de 200 semanas y su RSI + condiciones comunes."""
    res = {"ma200": None, "rsi": None, "dist": None, "rsi_v": None, "puntos": 0, "tramo": 0}
    V = V_MEMORIA
    if px_w is not None and len(px_w) > 210:
        ma = px_w.rolling(200).mean()
        res["ma200"] = bool((px_w <= 1.05 * ma).iloc[-V:].any())
        res["dist"] = (px_w.iloc[-1] / ma.iloc[-1] - 1) * 100
    if px_w is not None and len(px_w) > 30:
        rr = rsi(px_w)
        res["rsi"] = bool((rr.iloc[-V:] < 30).any())
        res["rsi_v"] = float(rr.iloc[-1])
    pts = 2 * bool(res["ma200"]) + 2 * bool(comun["credito"]) + bool(comun["vix"]) + bool(res["rsi"])
    res["puntos"] = int(pts)
    tramo = 3 if pts >= 6 else 2 if pts >= 4 else 1 if pts >= 2 else 0
    res["tramo"] = min(tramo, 1) if comun["veto"] else tramo
    return res


NIVELES = ["MUY MALAS", "MALAS", "REGULAR-PESIMISTA", "REGULAR-OPTIMISTA", "BUENAS", "MUY BUENAS"]
COLOR_NIVEL = {"MUY MALAS": "#8e1b1b", "MALAS": "#d9534f", "REGULAR-PESIMISTA": "#e0a33a",
               "REGULAR-OPTIMISTA": "#9cbf4a", "BUENAS": "#2e9d55", "MUY BUENAS": "#146b37"}


def condicion_indice(px_w, ref_w, comun, ecy, es_eeuu):
    if px_w is None or len(px_w) < 60:
        return None
    ma40 = px_w.rolling(40).mean()
    pendiente = ma40.iloc[-1] / ma40.iloc[-5] - 1
    cierre = px_w.iloc[-1]
    if cierre > ma40.iloc[-1] and pendiente > 0:
        tend = "alcista"
    elif cierre < ma40.iloc[-1] and pendiente < 0:
        tend = "bajista"
    else:
        tend = "lateral"
    fase, extra, Yv = None, {"alc": 0}, 100.0
    if ref_w is not None:
        rs, X, Y = rrg(px_w, ref_w)
        XY = pd.concat([X, Y], axis=1).dropna()
        if len(XY) >= 12:
            fase, extra = fase_motor(px_w.loc[:rs.index[-1]], rs, XY.iloc[:, 0], XY.iloc[:, 1])
            Yv = float(XY.iloc[-1, 1])
    pu = puerta_indice(px_w, comun)
    dist = pu["dist"]
    lejos = dist is None or dist > 10
    valor_ok = (not es_eeuu) or (ecy is not None and ecy >= 3)
    atractivo = (not lejos) and valor_ok
    if tend == "bajista":
        if comun["veto"]:
            n, m = "MUY MALAS", "Tendencia bajista confirmada y crédito deteriorándose con rapidez"
        elif lejos:
            n, m = "MUY MALAS", "Tendencia bajista confirmada y precio aún lejos de su media de 200 semanas"
        elif pu["tramo"] >= 2:
            n, m = "REGULAR-OPTIMISTA", "Bajista, pero cerca de la zona de suelo y con señales de suelo acumulándose"
        else:
            n, m = "REGULAR-PESIMISTA", "Bajista, cerca de la zona de suelo: esperar señales"
    elif tend == "alcista":
        if fase in ("MADUREZ", "GIRO") or extra.get("alc", 0) >= 2:
            n, m = "MALAS", f"Tendencia alcista con señales de fin de ciclo (fase {fase.lower() if fase else 'sin dato'})"
        elif fase in ("REZAGADO", "SUELO"):
            n, m = "REGULAR-OPTIMISTA", "Tendencia alcista, pero perdiendo fuerza frente a la liquidez"
        elif atractivo:
            n, m = "MUY BUENAS", "Tendencia alcista sana y precio atractivo"
        else:
            n, m = "BUENAS", "Tendencia alcista sana"
            if lejos:
                m += ", aunque el precio está lejos de su media de 200 semanas"
    else:
        if lejos and fase in ("GIRO", "REZAGADO"):
            n, m = "MALAS", "Sin tendencia, impulso débil y lejos de la zona de suelo"
        elif fase in ("ARRANQUE", "SUELO") or Yv >= 100:
            n, m = "REGULAR-OPTIMISTA", "Sin tendencia clara, pero el impulso mejora"
        else:
            n, m = "REGULAR-PESIMISTA", "Sin tendencia clara y con el impulso débil"
    if es_eeuu and ecy is not None and ecy < 1.5 and n in ("BUENAS", "MUY BUENAS"):
        m += " · valoración muy exigente (ECY bajo)"
    return {"nivel": n, "motivo": m, "tend": tend, "fase": fase, "dist": dist,
            "puntos": pu["puntos"], "tramo": pu["tramo"], "pu": pu}


# ----------------------------------------------------------------------------
# CALENDARIO
# ----------------------------------------------------------------------------

def tercer_viernes(anio, mes):
    d = dt.date(anio, mes, 1)
    primero = d + dt.timedelta(days=(4 - d.weekday()) % 7)
    return primero + dt.timedelta(days=14)


def ultimo_viernes(anio, mes):
    d = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - 4) % 7)


def segundo_viernes(anio, mes):
    d = dt.date(anio, mes, 1)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7 + 7)


def ultimo_habil(anio, mes):
    d = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
    while d.weekday() > 4:
        d -= dt.timedelta(days=1)
    return d


def calendario(dias=120):
    hoy = HOY.date()
    fin = hoy + dt.timedelta(days=dias)
    ev = []
    for anio in range(hoy.year, fin.year + 1):
        for mes in range(1, 13):
            tv = tercer_viernes(anio, mes)
            if mes in (3, 6, 9, 12):
                ev.append((tv, "Vencimiento trimestral de opciones y futuros + rebalanceo del S&P 500", "Opciones"))
            else:
                ev.append((tv, "Vencimiento mensual de opciones", "Opciones"))
            sig = (anio + (mes // 12), mes % 12 + 1)
            vix = tercer_viernes(*sig) - dt.timedelta(days=30)
            ev.append((vix, "Vencimiento del VIX", "Opciones"))
            fm = ultimo_habil(anio, mes)
            if mes == 12:
                ev.append((fm, "Cierre de año (cierre de libros)", "Cierres"))
            elif mes in (3, 6, 9):
                ev.append((fm, "Cierre de trimestre (cierre de libros, rebalanceo de fondos de pensiones)", "Cierres"))
            # Blackout estimado de recompras
            if mes in (3, 6, 9, 12):
                fin_t = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
                ini_b = fin_t - dt.timedelta(days=20)
                fin_b = fin_t + dt.timedelta(days=24)
                ev.append((ini_b, "Empieza el blackout de recompras (estimado)", "Recompras"))
                ev.append((fin_b, "Termina el blackout de recompras (estimado)", "Recompras"))
        ev.append((ultimo_viernes(anio, 6), "Reconstitución Russell (junio), efectiva al cierre", "Índices"))
        ev.append((segundo_viernes(anio, 12), "Reconstitución Russell (diciembre), efectiva al cierre", "Índices"))
        ev.append((ultimo_habil(anio, 4), "Russell: día de referencia (junio)", "Índices"))
        ev.append((ultimo_habil(anio, 10), "Russell: día de referencia (diciembre)", "Índices"))
        ev.append((dt.date(anio, 3, 31), "Fin del año fiscal en Japón", "Fiscal"))
        ev.append((dt.date(anio, 4, 15), "Día de impuestos en EE. UU.", "Fiscal"))
        ev.append((dt.date(anio, 10, 31), "Fin del ejercicio fiscal de muchos fondos estadounidenses", "Fiscal"))
        ev.append((dt.date(anio, 12, 1), "Empieza la ventana de ventas por pérdidas fiscales", "Fiscal"))
    for a, b in FOMC:
        ev.append((dt.date.fromisoformat(b), f"Reunión de la Fed (FOMC) {a[8:]}-{b[8:]}", "Fed"))
    for f in IPC_EEUU:
        ev.append((dt.date.fromisoformat(f), "IPC EE. UU.", "Datos"))
    ev = sorted({(d, t, c) for d, t, c in ev if hoy <= d <= fin})
    # ¿Estamos en blackout estimado?
    en_blackout = False
    for anio in (hoy.year - 1, hoy.year):
        for mes in (3, 6, 9, 12):
            fin_t = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
            if fin_t - dt.timedelta(days=20) <= hoy <= fin_t + dt.timedelta(days=24):
                en_blackout = True
    return ev, en_blackout


def estacionalidad(sh, spy_diario):
    out = {}
    if sh is not None:
        p = sh["P"].dropna()
        ret = p.pct_change().dropna() * 100
        tabla = pd.DataFrame({"r": ret.values, "mes": ret.index.month})
        g = tabla.groupby("mes")["r"]
        out["meses"] = pd.DataFrame({"media": g.mean(), "mediana": g.median(),
                                     "positivos": g.apply(lambda x: (x > 0).mean() * 100)})
        anual = p.resample("YS").last().pct_change().dropna() * 100
        anual = anual[anual.index.year < HOY.year]
        ciclo = pd.DataFrame({"r": anual.values, "c": anual.index.year % 4})
        nombres = {0: "Año electoral", 1: "Año 1 tras elecciones", 2: "Año de medio mandato", 3: "Año preelectoral"}
        gc = ciclo.groupby("c")["r"]
        out["ciclo"] = [(nombres[k], gc.mean()[k], gc.median()[k], (ciclo[ciclo.c == k].r > 0).mean() * 100)
                        for k in (1, 2, 3, 0)]
        out["anio_actual"] = nombres[HOY.year % 4]
        out["desde"] = p.index[0].year
    if spy_diario is not None and len(spy_diario) > 500:
        r = spy_diario.pct_change().dropna() * 100
        df = pd.DataFrame({"r": r})
        df["ym"] = df.index.to_period("M")
        df["n"] = df.groupby("ym").cumcount()
        df["m"] = df.groupby("ym")["r"].transform("count") - df["n"]
        cambio = (df["m"] <= 1) | (df["n"] <= 2)
        out["cambio_mes"] = (df[cambio]["r"].mean(), df[~cambio]["r"].mean(), spy_diario.index[0].year)
    return out


# ----------------------------------------------------------------------------
# GRÁFICOS SVG
# ----------------------------------------------------------------------------

def svg_rrg(puntos, titulo_x, titulo_y, centro=100.0, rango=None, ancho=680, alto=520,
            cuadrantes=("LIDERANDO", "DEBILITÁNDOSE", "REZAGADO", "MEJORANDO")):
    """puntos: lista de dict(et, xs, ys, color)."""
    xs = [v for p in puntos for v in p["xs"]]
    ys = [v for p in puntos for v in p["ys"]]
    if not xs:
        return "<p class='nota'>Sin datos suficientes.</p>"
    if rango is None:
        dx = max(abs(max(xs) - centro), abs(min(xs) - centro), 1) * 1.15
        dy = max(abs(max(ys) - centro), abs(min(ys) - centro), 1) * 1.15
        x0, x1, y0, y1 = centro - dx, centro + dx, centro - dy, centro + dy
    else:
        x0, x1, y0, y1 = rango
    m = 40

    def X(v):
        return m + (v - x0) / (x1 - x0) * (ancho - 2 * m)

    def Y(v):
        return alto - m - (v - y0) / (y1 - y0) * (alto - 2 * m)

    cx, cy = X(centro), Y(centro)
    s = [f'<svg viewBox="0 0 {ancho} {alto}" class="grafico" role="img">']
    s.append(f'<rect x="{cx}" y="{m}" width="{ancho - m - cx}" height="{cy - m}" fill="#e8f6ee"/>')
    s.append(f'<rect x="{cx}" y="{cy}" width="{ancho - m - cx}" height="{alto - m - cy}" fill="#fdf5dc"/>')
    s.append(f'<rect x="{m}" y="{cy}" width="{cx - m}" height="{alto - m - cy}" fill="#fbe9e7"/>')
    s.append(f'<rect x="{m}" y="{m}" width="{cx - m}" height="{cy - m}" fill="#e7effc"/>')
    s.append(f'<line x1="{cx}" y1="{m}" x2="{cx}" y2="{alto - m}" stroke="#999" stroke-width="1"/>')
    s.append(f'<line x1="{m}" y1="{cy}" x2="{ancho - m}" y2="{cy}" stroke="#999" stroke-width="1"/>')
    et = [(cuadrantes[0], ancho - m - 6, m + 16, "end", "#1f7a45"),
          (cuadrantes[1], ancho - m - 6, alto - m - 8, "end", "#9a7400"),
          (cuadrantes[2], m + 6, alto - m - 8, "start", "#a33"),
          (cuadrantes[3], m + 6, m + 16, "start", "#2457b0")]
    for t, x, y, a, c in et:
        s.append(f'<text x="{x}" y="{y}" text-anchor="{a}" class="cuad" fill="{c}">{t}</text>')
    s.append(f'<text x="{ancho / 2}" y="{alto - 8}" text-anchor="middle" class="eje">{e(titulo_x)} →</text>')
    s.append(f'<text x="14" y="{alto / 2}" text-anchor="middle" class="eje" transform="rotate(-90 14 {alto / 2})">{e(titulo_y)} →</text>')
    for p in puntos:
        s.append(f'<g class="serie" data-t="{e(p["et"])}" data-g="{e(p.get("g", ""))}" data-tip="{e(p.get("tip", p["et"]))}">')
        pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in zip(p["xs"], p["ys"]))
        s.append(f'<polyline class="r" points="{pts}" fill="none" stroke="{p["color"]}" stroke-width="1.6" stroke-opacity="0.55"/>')
        for a, b in zip(p["xs"][1:-1], p["ys"][1:-1]):
            s.append(f'<circle class="r" cx="{X(a):.1f}" cy="{Y(b):.1f}" r="1.6" fill="{p["color"]}" fill-opacity="0.45"/>')
        a0, b0 = p["xs"][0], p["ys"][0]
        s.append(f'<rect class="r" x="{X(a0) - 3.5:.1f}" y="{Y(b0) - 3.5:.1f}" width="7" height="7" fill="#fff" stroke="{p["color"]}" stroke-width="1.6"/>')
        a, b = p["xs"][-1], p["ys"][-1]
        s.append(f'<circle cx="{X(a):.1f}" cy="{Y(b):.1f}" r="4.5" fill="{p["color"]}"/>')
        if X(a) > ancho - 110:
            s.append(f'<text x="{X(a) - 6:.1f}" y="{Y(b) - 5:.1f}" text-anchor="end" class="etq">{e(p["et"])}</text>')
        else:
            s.append(f'<text x="{X(a) + 6:.1f}" y="{Y(b) - 5:.1f}" class="etq">{e(p["et"])}</text>')
        s.append("</g>")
    s.append("</svg>")
    return "".join(s)


def svg_linea(serie, alto=120, ancho=680, color="#2f6fdb", ref=None):
    s = serie.dropna()
    if len(s) < 2:
        return ""
    v = s.values
    lo, hi = float(np.min(v)), float(np.max(v))
    if ref is not None:
        lo, hi = min(lo, ref), max(hi, ref)
    if hi == lo:
        hi = lo + 1
    m = 6
    pts = " ".join(f"{m + i / (len(v) - 1) * (ancho - 2 * m):.1f},{alto - m - (x - lo) / (hi - lo) * (alto - 2 * m):.1f}"
                   for i, x in enumerate(v))
    out = [f'<svg viewBox="0 0 {ancho} {alto}" class="mini">']
    if ref is not None:
        yr = alto - m - (ref - lo) / (hi - lo) * (alto - 2 * m)
        out.append(f'<line x1="0" x2="{ancho}" y1="{yr:.1f}" y2="{yr:.1f}" stroke="#bbb" stroke-dasharray="4 4"/>')
    out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/></svg>')
    return "".join(out)


# ----------------------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ----------------------------------------------------------------------------

def main():
    os.makedirs(CARPETA_WEB, exist_ok=True)
    open(os.path.join(CARPETA_WEB, ".nojekyll"), "w").close()

    # --- Descargas
    precios = {}
    for t in [m for m, _ in MOTORES] + [REFERENCIA] + OTROS_TIINGO:
        s = seguro(f"Precio {t}", tiingo, t, "daily" if t == "SPY" else "weekly")
        if s is not None:
            precios[t] = s
            registrar(f"Precio {t}", s[s.index <= HOY])
        time.sleep(0.5)
    fr = {}
    for sid in FRED_SERIES:
        s = seguro(FRED_SERIES[sid], fred, sid)
        if s is not None:
            fr[sid] = s
            registrar(FRED_SERIES[sid], s)
    cb = {}
    for n in CBOE_INDICES:
        s = seguro(f"Cboe {n}", cboe_indice, n)
        if s is not None:
            cb[n] = s
            registrar(f"Cboe {n}", s)
    sh = seguro("CAPE / ECY (Shiller)", shiller)
    if sh is not None:
        registrar("CAPE / ECY (Shiller)", sh["CAPE"].dropna())
    fci = seguro("FCI-G (Reserva Federal)", fcig)
    registrar("FCI-G (Reserva Federal)", fci)
    pc = seguro("Ratio put/call (Cboe)", actualizar_putcall)
    if pc is not None:
        registrar("Ratio put/call (Cboe)", pc)
    spy_ult = float(precios["SPY"].iloc[-1]) if "SPY" in precios else None
    gm = seguro("Gamma SPX (Cboe)", gamma_spx, spy_ult)
    cot_series = {}
    for etq, ds, cod, l, c in [
        ("S&P 500 · gestoras", "gpe5-46if", "13874A", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("S&P 500 · fondos apalancados", "gpe5-46if", "13874A", "lev_money_positions_long", "lev_money_positions_short"),
        ("Nasdaq 100 · gestoras", "gpe5-46if", "209742", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("Nasdaq 100 · fondos apalancados", "gpe5-46if", "209742", "lev_money_positions_long", "lev_money_positions_short"),
        ("VIX · fondos apalancados", "gpe5-46if", "1170E1", "lev_money_positions_long", "lev_money_positions_short"),
        ("VIX · gestoras", "gpe5-46if", "1170E1", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("Bono 10 años · fondos apalancados", "gpe5-46if", "043602", "lev_money_positions_long", "lev_money_positions_short"),
        ("Bono 10 años · gestoras", "gpe5-46if", "043602", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("Oro · especuladores", "72hh-3qpy", "088691", "m_money_positions_long_all", "m_money_positions_short_all"),
        ("Petróleo WTI · especuladores", "72hh-3qpy", "067651", "m_money_positions_long_all", "m_money_positions_short_all"),
        ("Cobre · especuladores", "72hh-3qpy", "085692", "m_money_positions_long_all", "m_money_positions_short_all"),
    ]:
        s = seguro(f"Posicionamiento {etq}", cot, ds, cod, l, c)
        if s is not None:
            cot_series[etq] = s
    if cot_series:
        registrar("Posicionamiento (CFTC)", max(cot_series.values(), key=lambda x: x.index[-1]))

    # --- Series semanales
    W = {t: semanal(s) for t, s in precios.items()}
    ref_w = W.get(REFERENCIA)

    # --- Mapa de rotación de motores
    motores = []
    if ref_w is not None:
        for t, nombre in MOTORES:
            if t not in W or W[t] is None:
                continue
            rs, X, Y = rrg(W[t], ref_w)
            if len(Y) < 12:
                continue
            XY = pd.concat([X, Y], axis=1).dropna()
            X, Y = XY.iloc[:, 0], XY.iloc[:, 1]
            f, extra = fase_motor(W[t].loc[:rs.index[-1]], rs, X, Y)
            motores.append({"t": t, "nombre": nombre, "X": X.iloc[-1], "Y": Y.iloc[-1],
                            "xs": list(X.iloc[-24:].values), "ys": list(Y.iloc[-24:].values),
                            "dy4": float(Y.iloc[-1] - Y.iloc[-5]),
                            "fase": f, "rsi": extra["rsi"], "alc": extra["alc"], "baj": extra["baj"],
                            "fecha": X.index[-1]})

    # --- Variables
    baa_w = semanal(fr.get("BAA10Y"))
    vix_w = semanal(cb.get("VIX"))
    vix3_w = semanal(cb.get("VIX3M"))
    ratio_vix_w = (vix_w / vix3_w).dropna() if (vix_w is not None and vix3_w is not None) else None
    acwi_w = W.get("ACWI")

    ecy = cape = fecha_ecy = None
    if sh is not None:
        ss = sh.dropna(subset=["ECY"])
        if len(ss):
            ecy = float(ss["ECY"].iloc[-1])
            cape = float(ss["CAPE"].iloc[-1])
            fecha_ecy = ss.index[-1]
    comun = puerta_comun(baa_w, ratio_vix_w)
    conds = []
    for t, nom, eeuu in INDICES_COND:
        c = condicion_indice(W.get(t), ref_w, comun, ecy, eeuu)
        if c:
            c.update({"t": t, "nombre": nom})
            conds.append(c)
    if ecy is None:
        val_txt = "sin dato"
    elif ecy < 1.5:
        val_txt = "MUY CARA"
    elif ecy < 3:
        val_txt = "CARA"
    elif ecy < 4.5:
        val_txt = "RAZONABLE"
    else:
        val_txt = "BARATA"

    # TOP 3
    top_salir = sorted([m for m in motores if m["fase"] in ("GIRO", "MADUREZ")],
                       key=lambda z: (0 if z["fase"] == "GIRO" else 1, z["dy4"]))[:3]
    top_ampliar = sorted([m for m in motores if m["fase"] == "ACELERACIÓN" and m["alc"] <= 1],
                         key=lambda z: -z["Y"])[:3]
    top_construir = sorted([m for m in motores if m["fase"] == "ARRANQUE"], key=lambda z: -z["Y"])[:3]

    # Liquidez neta de la Fed (millones de $)
    liq_w = None
    if all(k in fr for k in ("WALCL", "WTREGEN", "RRPONTSYD")):
        a = semanal(fr["WALCL"])
        b = semanal(fr["WTREGEN"])
        c = semanal(fr["RRPONTSYD"]) * 1000
        liq_w = pd.concat([a, b, c], axis=1).ffill().dropna()
        liq_w = (liq_w.iloc[:, 0] - liq_w.iloc[:, 1] - liq_w.iloc[:, 2]) / 1e6  # billones de $

    macro = []

    def add(v):
        if v is not None:
            macro.append(v)

    if "CPIAUCSL" in fr:
        cpi_m = mensual(fr["CPIAUCSL"])
        add(variable_macro("Inflación (IPC interanual, %)", (cpi_m / cpi_m.shift(12) - 1).dropna() * 100, "m", 6))
    enc = [mensual(fr[k]) for k in ("GACDFSA066MSFRBPHI", "GACDISA066MSFRBNY") if k in fr]
    if enc:
        crec = pd.concat(enc, axis=1).dropna().mean(axis=1).rolling(3).mean().dropna()
        add(variable_macro("Crecimiento (encuestas Fed, media 3 meses)", crec, "m", 6))
    if baa_w is not None:
        add(variable_macro("Estrés de crédito (Baa − bono 10 años)", baa_w, "s", 13))
    if liq_w is not None:
        add(variable_macro("Liquidez neta de la Fed (billones $)", liq_w, "s", 13, "pct"))
    for sid, per in (("NFCI", 13), ("STLFSI4", 13), ("T10Y3M", 13), ("DFII10", 13), ("T10YIE", 13)):
        if sid in fr:
            add(variable_macro(FRED_SERIES[sid], semanal(fr[sid]), "s", per))
    if "DTWEXBGS" in fr:
        add(variable_macro("Dólar (índice amplio)", semanal(fr["DTWEXBGS"]), "s", 13, "pct"))
    if "ICSA" in fr:
        add(variable_macro("Peticiones de subsidio (media 4 semanas)", semanal(fr["ICSA"]).rolling(4).mean().dropna(), "s", 13, "pct"))
    if "SAHMREALTIME" in fr:
        add(variable_macro("Regla de Sahm", mensual(fr["SAHMREALTIME"]), "m", 6))
    if fci is not None:
        add(variable_macro("Condiciones financieras FCI-G (Fed)", mensual(fci), "m", 6))
    if "CPER" in W and "GLD" in W and W["CPER"] is not None and W["GLD"] is not None:
        cg = (W["CPER"] / W["GLD"]).dropna()
        add(variable_macro("Cobre / oro", cg, "s", 13, "pct"))
    if ratio_vix_w is not None:
        add(variable_macro("Estructura del VIX (VIX/VIX3M)", ratio_vix_w, "s", 4))
    for n, nom in (("DSPX", "Dispersión (DSPX)"), ("COR3M", "Correlación implícita (COR3M)")):
        if n in cb:
            add(variable_macro(nom, semanal(cb[n]), "s", 13))
    if "BAMLH0A0HYM2" in fr:
        add(variable_macro("Diferencial high yield (solo 3 años)", semanal(fr["BAMLH0A0HYM2"]), "s", 13))
    if "RSP" in W and "SPY" in W and W["RSP"] is not None and W["SPY"] is not None:
        add(variable_macro("Equiponderado / capitalización (RSP/SPY)", (W["RSP"] / W["SPY"]).dropna(), "s", 13, "pct"))
    mac = {v["nombre"]: v for v in macro}

    # --- Sentimiento (réplica Euphoriameter)
    sent = None
    comp = []
    if sh is not None:
        comp.append(pct(sh["CAPE"].dropna()))
    if vix_w is not None:
        comp.append(100 - pct(vix_w))
    if acwi_w is not None and len(acwi_w) > 210:
        dist = (acwi_w / acwi_w.rolling(200).mean() - 1).dropna()
        comp.append(pct(dist))
    if comp:
        sent = float(np.mean(comp))

    # --- Convicción
    votos = {}
    crec_v = mac.get("Crecimiento (encuestas Fed, media 3 meses)")
    sub_v = mac.get("Peticiones de subsidio (media 4 semanas)")
    sahm = fr["SAHMREALTIME"].iloc[-1] if "SAHMREALTIME" in fr else None
    if crec_v and sub_v:
        mejora_c = crec_v["impulso"] > 0
        mejora_s = sub_v["impulso"] < 0
        if (sahm is not None and sahm >= 0.5) or (not mejora_c and not mejora_s):
            votos["A · Régimen"] = -1
        elif mejora_c and mejora_s:
            votos["A · Régimen"] = 1
        else:
            votos["A · Régimen"] = 0
    if baa_w is not None and ratio_vix_w is not None and len(baa_w) > 14:
        sube = baa_w.iloc[-1] > baa_w.iloc[-14]
        rv = ratio_vix_w.iloc[-1]
        votos["B · Estrés"] = 1 if (not sube and rv < 1) else -1 if (sube and rv > 1) else 0
    if liq_w is not None and "NFCI" in fr:
        nf = semanal(fr["NFCI"])
        l_sube = liq_w.iloc[-1] > liq_w.iloc[-14]
        n_baja = nf.iloc[-1] < nf.iloc[-14]
        votos["C · Liquidez"] = 1 if (l_sube and n_baja) else -1 if (not l_sube and not n_baja) else 0
    if sent is not None:
        votos["E+F+G · Valoración, técnico y sentimiento"] = -1 if sent > 80 else 1 if sent < 20 else 0
    fase_acwi = next((m["fase"] for m in motores if m["t"] == "ACWI"), None)
    direccion = None
    if fase_acwi:
        direccion = 1 if fase_acwi in ("SUELO", "ARRANQUE", "ACELERACIÓN") else -1
    coinciden = sum(1 for v in votos.values() if direccion is not None and v == direccion)
    conv = "BAJA" if coinciden <= 1 else "MEDIA" if coinciden <= 3 else "ALTA"
    ritmo = {"BAJA": "No construir", "MEDIA": "Completar el tramo en 2–3 meses", "ALTA": "Completar el tramo en 1 mes"}[conv]
    d_txt = None
    if "DSPX" in cb and "COR3M" in cb:
        p_d, p_c = pct(cb["DSPX"]), pct(cb["COR3M"])
        d_txt = ("Favorable a selección de valores (alfa)" if (p_d > 60 and p_c < 40)
                 else "Favorable a índice (beta)" if (p_d < 40 and p_c > 60) else "Neutral")

    # --- Calendario y estacionalidad
    eventos, en_blackout = calendario()
    est = estacionalidad(sh, precios.get("SPY"))

    # ------------------------------------------------------------------------
    # HTML
    # ------------------------------------------------------------------------
    H = []
    H.append(CABECERA.replace("{{FECHA}}", f"{AHORA + pd.Timedelta(hours=1):%d-%m-%Y %H:%M}"))

    # ===== PESTAÑA 1: DIAGNOSTICADOR
    H.append('<section id="t1" class="pest activa">')

    # Condiciones de indexación
    H.append(f'<h2 class="primero">Condiciones de indexación{i("condiciones")}</h2>')
    H.append('<table><tr><th>Índice</th><th>Condiciones</th><th>Por qué</th><th>Tendencia</th><th>Fase</th>'
             f'<th>Distancia a media 200 sem.</th><th>Puerta{i("puntos")}</th></tr>')
    for c in conds:
        H.append(f'<tr><td><b>{e(c["nombre"])}</b> ({tk(c["t"])})</td>'
                 f'<td><span class="nivel" style="background:{COLOR_NIVEL[c["nivel"]]}">{c["nivel"]}</span></td>'
                 f'<td class="motivo">{e(c["motivo"])}</td><td>{c["tend"]}</td>'
                 f'<td>{e((c["fase"] or "—").capitalize())}</td><td>{fmt(c["dist"], 1, " %")}</td>'
                 f'<td>{c["puntos"]}/6 · tramo {c["tramo"]}</td></tr>')
    H.append('</table>')

    # TOP 3
    def lista_top(ms):
        if not ms:
            return '<p class="nota">Ningún motor cumple ahora el criterio.</p>'
        return "<ol>" + "".join(f'<li>{tk(m["t"])} <span class="nota">{e(m["nombre"])}</span></li>' for m in ms) + "</ol>"
    H.append('<div class="tarjetas tops">')
    H.append(f'<div class="tarjeta top salir"><div class="t">Cerca de fin de ciclo · ir saliendo{i("top_salir")}</div>{lista_top(top_salir)}</div>')
    H.append(f'<div class="tarjeta top ampliar"><div class="t">Tendencia alcista con recorrido · ampliar{i("top_ampliar")}</div>{lista_top(top_ampliar)}</div>')
    H.append(f'<div class="tarjeta top construir"><div class="t">Podrían iniciar tendencia alcista · empezar a construir{i("top_construir")}</div>{lista_top(top_construir)}</div>')
    H.append('</div>')

    # Tarjetas de contexto
    H.append('<div class="tarjetas" style="margin-top:10px">')
    H.append(f'<div class="tarjeta"><div class="t">Valoración EE. UU. (ECY){i("valoracion")}</div><div class="v">{val_txt}</div>'
             f'<div class="s">ECY {fmt(ecy, 2, " %")} · CAPE {fmt(cape, 1)}'
             f'{" · " + format(fecha_ecy, "%m-%Y") if fecha_ecy is not None else ""}</div></div>')
    vt = "ACTIVO" if comun["veto"] else "no"
    H.append(f'<div class="tarjeta {"alerta" if comun["veto"] else ""}"><div class="t">Veto por crédito{i("veto")}</div><div class="v">{vt}</div>'
             f'<div class="s">Baa−10a a 13 semanas: {fmt(comun.get("baa_13s"), 1, " %")}</div></div>')
    H.append(f'<div class="tarjeta"><div class="t">Convicción{i("conviccion")}</div><div class="v">{conv}</div>'
             f'<div class="s">{e(ritmo)}</div></div>')
    H.append(f'<div class="tarjeta"><div class="t">Sentimiento (0–100){i("sentimiento")}</div><div class="v">{fmt(sent, 0)}</div>'
             f'<div class="s">0 = miedo · 100 = euforia</div></div>')
    H.append('</div>')

    # Puerta por índice
    def si_no(v, dato=""):
        if v is None:
            return "<td>sin dato</td>"
        return f'<td class="{"si" if v else ""}">{"SÍ" if v else "no"}{(" · " + dato) if dato else ""}</td>'
    rv = comun.get("ratio_vix")
    rv_txt = "" if rv is None else f'VIX/VIX3M {fmt(rv, 2)}: {"contango, lo normal" if rv < 1 else "invertida, miedo agudo"}'
    H.append(f'<h2>Puerta de indexación{i("puntos")}</h2>')
    H.append(f'<table><tr><th>Índice</th><th>Cerca de la media de 200 sem. (2){i("c_ma200")}</th>'
             f'<th>Crédito girando (2){i("c_credito")}</th><th>VIX vuelve a contango (1){i("c_vix")}</th>'
             f'<th>RSI semanal en sobreventa (1){i("c_rsi")}</th><th>Puntos</th><th>Tramo</th></tr>')
    for c in conds:
        pu = c["pu"]
        H.append(f'<tr><td>{e(c["nombre"])}</td>{si_no(pu["ma200"], "a " + fmt(pu["dist"], 1, " %"))}'
                 f'{si_no(comun["credito"])}{si_no(comun["vix"])}{si_no(pu["rsi"], "RSI " + fmt(pu["rsi_v"], 0))}'
                 f'<td><b>{c["puntos"]}/6</b></td><td>{c["tramo"]} de 3</td></tr>')
    H.append(f'</table><p class="nota">Crédito y VIX son comunes a todos los índices. {e(rv_txt)}. '
             'Cada condición cuenta si se cumplió en las últimas 13 semanas. 2, 4 y 6 puntos abren los tramos 1, 2 y 3.</p>')

    H.append(f'<h2>Grado de convicción{i("conviccion")}</h2><table><tr><th>Bloque</th><th>Voto</th></tr>')
    for k, v in votos.items():
        H.append(f'<tr><td>{e(k)}{i(CLAVE_BLOQUE.get(k, ""))}</td><td>{"a favor del riesgo" if v > 0 else "en contra del riesgo" if v < 0 else "neutral"}</td></tr>')
    H.append(f'<tr><td>Dirección del mapa (RV global){i("direccion")}</td><td>{e(fase_acwi or "sin dato")}</td></tr>')
    H.append(f'<tr><td>D · Beta o alfa{i("b_D")}</td><td>{e(d_txt or "sin dato")}</td></tr>')
    H.append(f'<tr><td>Sentimiento (réplica Euphoriameter, 0–100){i("sentimiento")}</td><td>{fmt(sent, 0)}</td></tr>')
    H.append('</table>')

    # Mapa motores
    H.append(f'<h2>Mapa de rotación de motores{i("mapa")}</h2>')
    H.append(f'<p class="nota">Eje horizontal: fuerza relativa{i("fuerza")} · eje vertical: impulso{i("impulso")}. '
             'Rastro de 24 semanas con cierres semanales: □ inicio · ● ahora. Pasa el cursor por un motor para resaltarlo y ver qué es.</p>')
    grupos_presentes = [g for g in COLOR_GRUPO if any(GRUPO.get(m["t"]) == g for m in motores)]
    H.append('<div class="leyenda">' + "".join(
        f'<label><input type="checkbox" class="fg" value="{e(g)}" checked> <span class="chip" style="background:{COLOR_GRUPO[g]}"></span>{e(g)}</label>'
        for g in grupos_presentes) + '<label><input type="checkbox" class="rastros" data-mapa="mapa-motores" checked> <b>Mostrar rastros</b></label></div>')
    H.append('<div class="filtros">' + "".join(
        f'<label data-tip="{e(m["t"] + " · " + DESC.get(m["t"], ""))}"><input type="checkbox" class="fm" value="{m["t"]}" data-g="{e(GRUPO.get(m["t"], ""))}" checked> {e(m["t"])}</label>'
        for m in motores) + '</div>')
    pts = [{"et": m["t"], "xs": m["xs"], "ys": m["ys"], "g": GRUPO.get(m["t"], ""),
            "color": COLOR_GRUPO.get(GRUPO.get(m["t"], ""), "#555"),
            "tip": m["t"] + " · " + DESC.get(m["t"], "") + " Fase: " + m["fase"].lower() + "."} for m in motores]
    H.append(f'<div id="mapa-motores">{svg_rrg(pts, "Fuerza relativa", "Impulso")}</div>')
    H.append(f'<table class="ord"><tr><th>Motor</th><th>Nombre</th><th>Grupo</th><th>Fase{i("fases")}</th><th>Qué hacer</th>'
             f'<th>Fuerza{i("fuerza")}</th><th>Impulso{i("impulso")}</th><th>RSI sem.</th><th>Agotamiento{i("agotamiento")}</th></tr>')
    orden = ["ARRANQUE", "ACELERACIÓN", "MADUREZ", "GIRO", "SUELO", "REZAGADO"]

    def agot_txt(m):
        partes = []
        if m["alc"]:
            partes.append(f'alcista {m["alc"]} de 3')
        if m["baj"]:
            partes.append(f'bajista {m["baj"]} de 3')
        return " · ".join(partes) or "—"
    for m in sorted(motores, key=lambda z: orden.index(z["fase"])):
        g = GRUPO.get(m["t"], "")
        H.append(f'<tr><td><b>{tk(m["t"])}</b></td><td>{e(m["nombre"])}</td>'
                 f'<td><span class="chip" style="background:{COLOR_GRUPO.get(g, "#555")}"></span>{e(g)}</td>'
                 f'<td><span class="fase" style="background:{COLOR_FASE[m["fase"]]}">{m["fase"]}</span></td>'
                 f'<td>{ACCION[m["fase"]]}</td><td>{fmt(m["X"], 1)}</td><td>{fmt(m["Y"], 1)}</td>'
                 f'<td>{fmt(m["rsi"], 0)}</td><td>{agot_txt(m)}</td></tr>')
    H.append('</table>')
    if motores:
        H.append(f'<p class="nota">Semana cerrada: {motores[0]["fecha"]:%d-%m-%Y}. Proxies en dólares (el euro se ve con FXE). Stock picking: ver bloque D. Market neutral: sin proxy.</p>')

    # Mapa macro
    clave = ["Inflación (IPC interanual, %)", "Crecimiento (encuestas Fed, media 3 meses)",
             "Estrés de crédito (Baa − bono 10 años)", "Liquidez neta de la Fed (billones $)"]
    cortos = {clave[0]: "Inflación", clave[1]: "Crecimiento", clave[2]: "Crédito (estrés)", clave[3]: "Liquidez"}
    color_macro = {"Inflación": "#c0392b", "Crecimiento": "#2f6fdb", "Crédito (estrés)": "#a0672a", "Liquidez": "#1f9d55"}
    pm = [{"et": cortos[k], "xs": mac[k]["rx"], "ys": mac[k]["ry"], "color": color_macro[cortos[k]], "g": cortos[k],
           "tip": cortos[k] + " · fase " + mac[k]["fase"].lower()}
          for k in clave if k in mac and len(mac[k]["rx"]) >= 1]
    H.append(f'<h2>Mapa macro{i("mapa_macro")}</h2><p class="nota">Eje horizontal: nivel{i("nivel_macro")} · eje vertical: impulso{i("impulso_macro")}. '
             'Rastro de 24 semanas: □ inicio · ● ahora (los datos mensuales tienen un punto por mes).</p>')
    H.append('<div class="leyenda">' + "".join(
        f'<span><span class="chip" style="background:{c}"></span>{e(g)}</span>' for g, c in color_macro.items()) + '</div>')
    H.append('<div id="mapa-macro">' + svg_rrg(pm, "Nivel (percentil)", "Impulso (percentil)", centro=50, rango=(0, 100, 0, 100),
                     cuadrantes=("ALTO Y SUBIENDO", "ALTO Y BAJANDO", "BAJO Y BAJANDO", "BAJO Y SUBIENDO")) + '</div>')

    H.append(f'<h2>Variables{i("variables")}</h2><table class="ord"><tr><th>Variable</th><th>Valor</th><th>Fecha</th><th>Percentil{i("nivel_macro")}</th><th>Cambio reciente{i("impulso_macro")}</th><th>Fase</th></tr>')
    for v in macro:
        f_ref = v["fecha"] + pd.offsets.MonthEnd(0) if v["frecuencia"] == "m" else v["fecha"]
        viejo = not antiguedad_ok(f_ref, "m" if v["frecuencia"] == "m" else "s")
        unidad = " %" if v["modo"] == "pct" else ""
        per = f'{v["periodos"]} {"meses" if v["frecuencia"] == "m" else "sem."}'
        H.append(f'<tr><td>{e(v["nombre"])}{i(v["nombre"])}</td><td>{fmt(v["valor"], 0 if abs(v["valor"]) >= 1000 else 2)}</td>'
                 f'<td class="{"viejo" if viejo else ""}">{v["fecha"]:%d-%m-%Y}{" ⚠" if viejo else ""}</td>'
                 f'<td>{fmt(v["nivel"], 0)}</td><td>{fmt(v["impulso"], 2, unidad)} ({per})</td>'
                 f'<td><span class="fase" style="background:{COLOR_FASE[v["fase"]]}">{v["fase"]}</span></td></tr>')
    H.append('</table><p class="nota">⚠ = dato desfasado.</p>')
    H.append('</section>')

    # ===== PESTAÑA 2: OPCIONES
    H.append('<section id="t2" class="pest">')
    H.append('<p class="nota">Señales de días a semanas: sirven para elegir el momento de cada tramo, no para el régimen.</p>')
    H.append('<div class="tarjetas">')
    if gm:
        signo = "POSITIVA" if gm["gex_mm"] > 0 else "NEGATIVA"
        efecto = "amortigua los movimientos" if gm["gex_mm"] > 0 else "amplifica los movimientos"
        hg = gm["hist"]["gex_mm"].dropna()
        inten = f"intensidad: percentil {fmt(pct(hg), 0)} de su histórico" if len(hg) >= 20 else "intensidad: histórico aún corto"
        H.append(f'<div class="tarjeta {"alerta" if gm["gex_mm"] < 0 else ""}"><div class="t">Gamma de los creadores de mercado (aprox.){i("gamma")}</div>'
                 f'<div class="v">{signo}</div><div class="s">{efecto} · {inten}</div></div>')
        if gm["giro"]:
            dist = (gm["spot"] / gm["giro"] - 1) * 100
            H.append(f'<div class="tarjeta"><div class="t">Nivel de giro de la gamma{i("giro")}</div><div class="v">{fmt(gm["giro"], 0)}</div>'
                     f'<div class="s">S&P 500 a {fmt(dist, 1, " %")} del giro</div></div>')
        H.append(f'<div class="tarjeta"><div class="t">Muros (próximos 70 días){i("muros")}</div><div class="v">{fmt(gm["muro_put"], 0)} – {fmt(gm["muro_call"], 0)}</div>'
                 f'<div class="s">soporte put · resistencia call</div></div>')
    else:
        H.append('<div class="tarjeta"><div class="t">Gamma</div><div class="v">sin dato</div></div>')
    if "SKEW" in cb:
        sk = cb["SKEW"]
        sk1 = sk[sk.index >= sk.index[-1] - pd.Timedelta(days=365)]
        H.append(f'<div class="tarjeta"><div class="t">SKEW{i("skew")}</div><div class="v">{fmt(sk.iloc[-1], 0)}</div>'
                 f'<div class="s">percentil {fmt(pct(sk), 0)} histórico (desde 1990) · {fmt(pct(sk1), 0)} último año · {sk.index[-1]:%d-%m}</div></div>')
    H.append('</div>')

    if pc is not None and len(pc):
        H.append(f'<h2>Ratio put/call{i("putcall")}</h2><table><tr><th>Tipo</th><th>Último</th><th>Media 10 días</th><th>Percentil (media 10 d.)</th></tr>')
        nombres_pc = {"total": "Total", "acciones": "Acciones", "indices": "Índices", "spx": "S&P 500"}
        for col, nom in nombres_pc.items():
            if col not in pc.columns:
                continue
            s = pc[col].dropna()
            if not len(s):
                continue
            m10 = s.rolling(10).mean().dropna()
            pp = fmt(pct(m10), 0) if len(m10) >= 60 else "historia corta"
            H.append(f'<tr><td>{nom}</td><td>{fmt(s.iloc[-1], 2)}</td><td>{fmt(m10.iloc[-1] if len(m10) else None, 2)}</td><td>{pp}</td></tr>')
        H.append(f'</table><p class="nota">Último dato: {pc.index[-1]:%d-%m-%Y}. Percentil alto = mucha cobertura (miedo); bajo = complacencia. Historia desde {pc.index[0]:%m-%Y}.</p>')
        if "acciones" in pc.columns:
            H.append(svg_linea(pc["acciones"].rolling(10).mean().dropna().iloc[-250:]))
            H.append('<p class="nota">Put/call de acciones, media de 10 días, último año.</p>')
    if gm and len(gm["hist"]) > 5:
        H.append(svg_linea(gm["hist"]["gex_mm"].dropna(), ref=0, color="#7a3fd1"))
        H.append('<p class="nota">Gamma neta, histórico propio.</p>')

    if cot_series:
        H.append(f'<h2>Posicionamiento (CFTC, semanal){i("cot")}</h2><table><tr><th>Mercado · grupo</th><th>Neto (% del interés abierto)</th>'
                 '<th>Cambio 4 semanas</th><th>Percentil 3 años</th><th>Fecha</th></tr>')
        for k, s in cot_series.items():
            s3 = s[s.index >= s.index[-1] - pd.Timedelta(days=3 * 365)]
            ch = s.iloc[-1] - s.iloc[-5] if len(s) > 5 else np.nan
            p3 = pct(s3)
            cls = "extremo" if (p3 >= 90 or p3 <= 10) else ""
            H.append(f'<tr><td>{e(k)}</td><td>{fmt(s.iloc[-1], 1, " %")}</td><td>{fmt(ch, 1)}</td>'
                     f'<td class="{cls}">{fmt(p3, 0)}</td><td>{s.index[-1]:%d-%m-%Y}</td></tr>')
        H.append('</table><p class="nota">Percentil ≥ 90 o ≤ 10 = posicionamiento extremo (resaltado).</p>')
    H.append('</section>')

    # ===== PESTAÑA 3: CALENDARIO
    H.append('<section id="t3" class="pest">')
    H.append(f'<div class="tarjetas"><div class="tarjeta {"alerta" if en_blackout else ""}"><div class="t">Blackout de recompras (estimado){i("blackout")}</div>'
             f'<div class="v">{"SÍ" if en_blackout else "NO"}</div><div class="s">estimación, no dato oficial</div></div>')
    if "anio_actual" in est:
        H.append(f'<div class="tarjeta"><div class="t">Ciclo presidencial{i("ciclo")}</div><div class="v" style="font-size:1.1em">{e(est["anio_actual"])}</div></div>')
    H.append('</div>')
    H.append('<h2>Próximos 120 días</h2><table><tr><th>Fecha</th><th>Evento</th><th>Tipo</th></tr>')
    dias_sem = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
    for d, t, c in eventos:
        H.append(f'<tr><td>{dias_sem[d.weekday()]} {d:%d-%m-%Y}</td><td>{e(t)}</td><td><span class="tipo">{e(c)}</span>{i(c)}</td></tr>')
    H.append('</table>')
    if "meses" in est:
        nm = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
        H.append(f'<h2>Estacionalidad del S&P 500 (desde {est["desde"]}){i("estacionalidad")}</h2>'
                 '<table><tr><th>Mes</th><th>Media</th><th>Mediana</th><th>% meses positivos</th></tr>')
        for mes, fila in est["meses"].iterrows():
            marca = ' class="hoy"' if mes in (HOY.month, HOY.month % 12 + 1) else ""
            H.append(f'<tr{marca}><td>{nm[mes - 1]}</td><td>{fmt(fila["media"], 2, " %")}</td>'
                     f'<td>{fmt(fila["mediana"], 2, " %")}</td><td>{fmt(fila["positivos"], 0, " %")}</td></tr>')
        H.append('</table>')
        H.append(f'<h2>Ciclo presidencial (rentabilidad anual){i("ciclo")}</h2><table><tr><th>Año del ciclo</th><th>Media</th><th>Mediana</th><th>% años positivos</th></tr>')
        for nom, me, md, po in est["ciclo"]:
            marca = ' class="hoy"' if nom == est["anio_actual"] else ""
            H.append(f'<tr{marca}><td>{nom}</td><td>{fmt(me, 1, " %")}</td><td>{fmt(md, 1, " %")}</td><td>{fmt(po, 0, " %")}</td></tr>')
        H.append('</table>')
    if "cambio_mes" in est:
        a, b, desde = est["cambio_mes"]
        H.append(f'<p class="nota">Cambio de mes (último día + 3 primeros, desde {desde}): media diaria {fmt(a, 3, " %")} frente a {fmt(b, 3, " %")} el resto de días.{i("cambio_mes")}</p>')
    H.append('<p class="nota">Evidencia académica de la estacionalidad: débil e inestable.</p>')
    H.append('</section>')

    # ===== Estado de las fuentes
    H.append('<section class="fuentes"><h2>Estado de los datos</h2>')
    if ERRORES:
        H.append('<p class="error"><b>Fuentes con fallo hoy:</b> ' + "; ".join(e(n) for n, _ in ERRORES) + '</p>')
    else:
        H.append('<p>Todas las fuentes se han descargado correctamente.</p>')
    H.append('<details><summary>Última fecha de cada fuente</summary><table>')
    for k in sorted(FECHAS):
        H.append(f'<tr><td>{e(k)}</td><td>{pd.Timestamp(FECHAS[k]):%d-%m-%Y}</td></tr>')
    H.append('</table></details></section>')

    # ===== Resumen en texto (para lectura automática)
    R = [f"RESUMEN {HOY:%d-%m-%Y}",
         "Condiciones: " + "; ".join(f"{c['nombre']}={c['nivel']} (puerta {c['puntos']}/6, tramo {c['tramo']})" for c in conds)
         + f"; valoración EE. UU.={val_txt} (ECY {fmt(ecy, 2)} %); veto={'sí' if comun['veto'] else 'no'}",
         "Top salir: " + ", ".join(m["t"] for m in top_salir) + " · ampliar: " + ", ".join(m["t"] for m in top_ampliar)
         + " · construir: " + ", ".join(m["t"] for m in top_construir),
         f"Convicción={conv}; votos=" + ", ".join(f"{k}:{v:+d}" for k, v in votos.items()) + f"; dirección RV global={fase_acwi}; beta/alfa={d_txt}; sentimiento={fmt(sent, 0)}",
         "Motores: " + "; ".join(f"{m['t']}={m['fase']}" for m in motores),
         "Macro: " + "; ".join(f"{v['nombre']}={v['fase']} (p{fmt(v['nivel'], 0)})" for v in macro)]
    if gm:
        R.append(f"Gamma={'positiva' if gm['gex_mm'] > 0 else 'negativa'}, giro={fmt(gm['giro'], 0)}, S&P={fmt(gm['spot'], 0)}")
    R.append(f"Blackout estimado={'sí' if en_blackout else 'no'}")
    if ERRORES:
        R.append("Fallos: " + ", ".join(n for n, _ in ERRORES))
    H.append('<pre class="resumen">' + e("\n".join(R)) + '</pre>')

    H.append('<div id="modal" class="modal"><div class="caja"><button class="cerrar" aria-label="Cerrar">×</button>'
             '<h3 id="modal-t"></h3><div id="modal-c"></div></div></div>')
    H.append('<script id="info-datos" type="application/json">' + info_json() + '</script>')
    H.append(PIE)
    with open(os.path.join(CARPETA_WEB, "index.html"), "w", encoding="utf-8") as f:
        f.write("".join(H))
    print("\n".join(R))
    for n, m in ERRORES:
        print("FALLO", n, m)


CABECERA = """<!DOCTYPE html>
<html lang="es" class="tema-azul"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diagnosticador de mercados</title>
<style>
html.tema-azul{--pagina:linear-gradient(170deg,#eef5fc 0%,#dce9f6 55%,#cfe0f2 100%);--panel:rgba(255,255,255,.72);--th:rgba(225,236,248,.95);--b:#c9d8e8;--f:#1b2a3a;--s:#56677a;--acento:#2f6fdb}
html.tema-gris{--pagina:linear-gradient(170deg,#f4f5f7 0%,#e6e9ee 100%);--panel:rgba(255,255,255,.8);--th:#eceff3;--b:#d8dde3;--f:#1d2733;--s:#5b6776;--acento:#4a5d75}
html.tema-blanco{--pagina:#ffffff;--panel:#f6f8fa;--th:#f6f8fa;--b:#e3e7ec;--f:#1d2733;--s:#5b6776;--acento:#2f6fdb}
html.tema-arena{--pagina:linear-gradient(170deg,#fbf8f2 0%,#f1eadc 100%);--panel:rgba(255,255,255,.75);--th:#f3ecdf;--b:#e2d7c3;--f:#2b2620;--s:#6c6253;--acento:#a0672a}
*{box-sizing:border-box}
html{background:var(--pagina);background-attachment:fixed;min-height:100%}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--f);background:transparent;margin:0 auto;padding:14px;max-width:1100px;font-size:15px;line-height:1.4}
header{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:10px}
h1{font-size:1.25em;margin:0}
.fecha{color:var(--s);font-size:.85em}
.apariencia{font-size:.8em;color:var(--s)}
.apariencia select{font-size:1em;border:1px solid var(--b);border-radius:6px;background:var(--panel);color:var(--f);padding:2px 4px}
nav{display:flex;gap:6px;border-bottom:1px solid var(--b);margin-bottom:14px;flex-wrap:wrap}
nav button{border:0;background:none;padding:9px 14px;font-size:1em;cursor:pointer;color:var(--s);border-bottom:3px solid transparent}
nav button.activa{color:var(--f);border-bottom-color:var(--acento);font-weight:600}
.pest{display:none}.pest.activa{display:block}
h2{font-size:1.05em;margin:22px 0 8px}
.tarjetas{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.tarjeta{background:var(--panel);border:1px solid var(--b);border-radius:10px;padding:10px 12px}
.tarjeta.alerta{background:#fff1ec;border-color:#f3b49b}
.tarjeta .t{font-size:.8em;color:var(--s)}.tarjeta .v{font-size:1.5em;font-weight:700;margin:2px 0}.tarjeta .s{font-size:.8em;color:var(--s)}
table{border-collapse:collapse;width:fit-content;max-width:100%;font-size:.88em;display:block;overflow-x:auto;background:var(--panel);border-radius:8px}
th,td{padding:6px 8px;border-bottom:1px solid var(--b);text-align:left;white-space:nowrap}
th{background:var(--th);font-weight:600;cursor:default}
table.ord th{cursor:pointer}
td.si{color:#1f7a45;font-weight:700}td.viejo{color:#c0392b}td.extremo{background:#fff1ec;font-weight:700}
tr.hoy td{background:rgba(47,111,219,.12);font-weight:600}
.fase{color:#fff;border-radius:6px;padding:2px 7px;font-size:.8em;font-weight:600}
.tipo{background:var(--th);border:1px solid var(--b);border-radius:6px;padding:1px 6px;font-size:.8em}
.nota{color:var(--s);font-size:.82em;margin:6px 0}
.grafico{width:100%;max-width:760px;height:auto;display:block;margin:6px 0;background:var(--panel);border-radius:10px}
.mini{width:100%;max-width:760px;height:auto;display:block;background:var(--panel);border-radius:8px;margin-top:8px}
.grafico .cuad{font-size:12px;font-weight:700;opacity:.7}.grafico .eje{font-size:12px;fill:#56677a}.grafico .etq{font-size:11px;font-weight:600;fill:#1b2a3a}
.filtros{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:.8em;margin:4px 0}
.fuentes{margin-top:26px;border-top:1px solid var(--b);padding-top:6px;font-size:.88em}
.error{color:#c0392b}
th{white-space:normal;vertical-align:bottom;min-width:60px}
.grafico.resaltando g.serie{opacity:.12}.grafico.resaltando g.serie.activa{opacity:1}
.grafico g.serie.activa polyline{stroke-width:3;stroke-opacity:.95}.grafico g.serie.activa text{font-size:13px}
.sin-rastro .r{display:none}
.nivel{color:#fff;border-radius:6px;padding:3px 8px;font-size:.8em;font-weight:700;white-space:nowrap}
td.motivo{white-space:normal;min-width:220px;max-width:340px}
h2.primero{margin-top:6px}
.tops{margin-top:12px}.tops .tarjeta ol{margin:6px 0 0;padding-left:20px}.tops .tarjeta li{margin:2px 0}
.top.salir{border-left:4px solid #d9534f}.top.ampliar{border-left:4px solid #2e9d55}.top.construir{border-left:4px solid #2f6fdb}
.chip{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:baseline}
.leyenda{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:.82em;margin:6px 0}
.tk{border-bottom:1px dotted currentColor;cursor:help}
#tip{position:fixed;display:none;z-index:60;max-width:300px;background:#1b2a3a;color:#fff;padding:7px 10px;border-radius:7px;font-size:12px;line-height:1.35;pointer-events:none;box-shadow:0 4px 14px rgba(0,0,0,.25)}
.info{border:0;background:none;color:var(--acento);cursor:pointer;font-size:.95em;padding:0 3px;line-height:1;vertical-align:baseline}
.modal{display:none;position:fixed;inset:0;background:rgba(20,30,45,.45);z-index:50;align-items:flex-start;justify-content:center;padding:36px 12px;overflow:auto}
.modal.abierto{display:flex}
.modal .caja{background:#fff;color:#1b2a3a;max-width:640px;width:100%;border-radius:12px;padding:16px 20px;box-shadow:0 10px 30px rgba(0,0,0,.2);position:relative;font-size:.93em;line-height:1.5}
.modal h3{margin:0 28px 8px 0;font-size:1.1em}.modal ul{padding-left:20px}
.modal .cerrar{position:absolute;top:8px;right:12px;border:0;background:none;font-size:1.5em;cursor:pointer;color:#56677a}
.fuentes-info{font-size:.85em;color:#56677a;border-top:1px solid #e3e7ec;padding-top:8px;margin-top:10px}
.fuentes-info a{color:#2f6fdb}
.resumen{white-space:pre-wrap;font-size:.75em;color:var(--s);background:var(--panel);padding:8px;border-radius:8px;margin-top:16px}
</style>
<script>try{var t=localStorage.getItem('tema');if(t){document.documentElement.className='tema-'+t;}}catch(e){}</script>
</head><body>
<header><h1>Diagnosticador de mercados</h1>
<span class="fecha">Actualizado {{FECHA}} (hora peninsular aprox.)</span>
<label class="apariencia">Apariencia
<select id="tema"><option value="azul">Azul suave</option><option value="gris">Gris claro</option><option value="arena">Arena</option><option value="blanco">Blanco</option></select></label>
</header>
<nav><button class="activa" data-t="t1">Diagnosticador</button><button data-t="t2">Opciones</button><button data-t="t3">Calendario</button></nav>
"""

PIE = r"""
<script>
(function(){var sel=document.getElementById('tema');var actual=document.documentElement.className.replace('tema-','');
 sel.value=actual;sel.addEventListener('change',function(){document.documentElement.className='tema-'+sel.value;
 try{localStorage.setItem('tema',sel.value);}catch(e){}});})();
(function(){var datos={};try{datos=JSON.parse(document.getElementById('info-datos').textContent);}catch(e){}
 var m=document.getElementById('modal');
 function cerrar(){m.classList.remove('abierto');}
 document.addEventListener('click',function(ev){var b=ev.target.closest&&ev.target.closest('.info');
  if(b){ev.preventDefault();ev.stopPropagation();var d=datos[b.getAttribute('data-k')];if(!d)return;
   document.getElementById('modal-t').textContent=d.t;document.getElementById('modal-c').innerHTML=d.h;m.classList.add('abierto');return;}
  if(ev.target===m||(ev.target.classList&&ev.target.classList.contains('cerrar')))cerrar();},true);
 document.addEventListener('keydown',function(ev){if(ev.key==='Escape')cerrar();});})();
(function(){var tip=document.createElement('div');tip.id='tip';document.body.appendChild(tip);
 document.addEventListener('mouseover',function(ev){var t=ev.target.closest&&ev.target.closest('[data-tip]');
  if(!t){tip.style.display='none';return;}tip.textContent=t.getAttribute('data-tip');tip.style.display='block';});
 document.addEventListener('mousemove',function(ev){if(tip.style.display!=='block')return;
  var x=ev.clientX+14,y=ev.clientY+16;if(x+310>window.innerWidth)x=Math.max(4,window.innerWidth-310);
  if(y+80>window.innerHeight)y=ev.clientY-60;tip.style.left=x+'px';tip.style.top=y+'px';});
 document.querySelectorAll('.fg').forEach(function(c){c.addEventListener('change',function(){
  document.querySelectorAll('.fm[data-g="'+c.value+'"]').forEach(function(m){m.checked=c.checked;
   m.dispatchEvent(new Event('change'));});});});})();
document.querySelectorAll('.grafico g.serie').forEach(function(g){var svg=g.ownerSVGElement;
 g.addEventListener('mouseenter',function(){svg.classList.add('resaltando');g.classList.add('activa');});
 g.addEventListener('mouseleave',function(){svg.classList.remove('resaltando');g.classList.remove('activa');});});
document.querySelectorAll('.rastros').forEach(function(c){c.addEventListener('change',function(){
 var m=document.getElementById(c.getAttribute('data-mapa'));if(m)m.classList.toggle('sin-rastro',!c.checked);});});
document.querySelectorAll('nav button').forEach(function(b){b.addEventListener('click',function(){
 document.querySelectorAll('nav button').forEach(function(x){x.classList.remove('activa')});
 document.querySelectorAll('.pest').forEach(function(x){x.classList.remove('activa')});
 b.classList.add('activa');document.getElementById(b.dataset.t).classList.add('activa');});});
document.querySelectorAll('.fm').forEach(function(c){c.addEventListener('change',function(){
 document.querySelectorAll('#mapa-motores g.serie').forEach(function(g){
  if(g.getAttribute('data-t')===c.value){g.style.display=c.checked?'':'none';}});});});
document.querySelectorAll('table.ord').forEach(function(tb){tb.querySelectorAll('th').forEach(function(th,i){
 th.addEventListener('click',function(){var filas=Array.from(tb.querySelectorAll('tr')).slice(1);
  var asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
  filas.sort(function(a,b){var x=a.children[i].innerText,y=b.children[i].innerText;
   var nx=parseFloat(x.replace(/\./g,'').replace(',','.')),ny=parseFloat(y.replace(/\./g,'').replace(',','.'));
   if(!isNaN(nx)&&!isNaN(ny))return asc?nx-ny:ny-nx;return asc?x.localeCompare(y):y.localeCompare(x);});
  filas.forEach(function(f){f.parentNode.appendChild(f);});});});});
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
