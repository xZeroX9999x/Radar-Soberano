"""Cálculo de precios objetivo y niveles de soporte técnico.

Implementa tres métodos clásicos de identificación de zonas de compra:

1. **Soportes por pivot points**: detecta mínimos locales históricos donde
   el mercado ya rebotó antes. Más confiables que líneas inventadas.

2. **Retroceso de Fibonacci**: niveles 38.2 / 50 / 61.8 sobre el rango
   anual. Estándar del análisis técnico desde los años 70.

3. **RSI implícito**: cálculo inverso — ¿a qué precio el RSI Wilder
   cruzaría el umbral de entrada? Matemático, no estimado.

El precio objetivo final combina los tres tomando el **más alto por debajo
del precio actual**, lo que produce un punto de entrada conservador pero
realista (comprás barato sin esperar un crash improbable).

Todas las funciones son **puras** — entran números, salen números. Sin I/O,
sin red, completamente testeables.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class PriceTargets:
    """Niveles de precio relevantes para tomar una decisión de entrada."""

    current_price: float
    suggested_buy_price: float
    distance_pct: float  # % desde precio actual al objetivo (negativo si arriba)
    supports: list[float]  # mínimos locales detectados (descendente)
    fibonacci_levels: dict[str, float]  # {"38.2%": ..., "50%": ..., "61.8%": ...}
    rsi_implied_price: float | None  # precio que llevaría el RSI a target
    high_52w: float
    low_52w: float
    pct_from_high: float  # qué tan abajo del máximo de 52sem (en %)
    method_used: str  # cuál método dio el precio final
    recommendation: str  # COMPRAR_AHORA / CERCA / ESPERAR / CARO
    recommendation_reason: str


def find_pivot_lows(
    closes: pd.Series,
    window: int = 10,
    max_results: int = 5,
) -> list[float]:
    """Detecta mínimos locales (pivot lows) en una serie de precios.

    Un pivot low es un punto donde el precio es menor que los `window` días
    anteriores Y los `window` días siguientes. Estos son niveles donde el
    mercado encontró soporte y rebotó.

    Args:
        closes: serie de precios de cierre.
        window: cuántos días a cada lado deben ser mayores.
        max_results: máximo de pivots a devolver.

    Returns:
        Lista de precios pivote, ordenados de mayor a menor.
    """
    if len(closes) < (2 * window + 1):
        return []

    pivots: list[float] = []
    values = closes.values

    for i in range(window, len(values) - window):
        candidate = values[i]
        is_local_min = (
            all(values[j] >= candidate for j in range(i - window, i)) and
            all(values[j] >= candidate for j in range(i + 1, i + window + 1))
        )
        if is_local_min:
            pivots.append(float(candidate))

    # Sin duplicados muy cercanos (consideramos < 2% de diferencia como mismo nivel)
    pivots.sort(reverse=True)
    deduped: list[float] = []
    for p in pivots:
        if not deduped or abs(p - deduped[-1]) / deduped[-1] > 0.02:
            deduped.append(p)

    return deduped[:max_results]


def fibonacci_retracements(
    high: float,
    low: float,
) -> dict[str, float]:
    """Niveles de retroceso de Fibonacci sobre un rango high-low.

    Devuelve los tres niveles canónicos: 38.2%, 50% y 61.8% medidos
    *desde el high*. Son zonas históricamente respetadas como soporte
    en una corrección.
    """
    if high <= low:
        return {}

    rng = high - low
    return {
        "38.2%": round(high - rng * 0.382, 2),
        "50%": round(high - rng * 0.500, 2),
        "61.8%": round(high - rng * 0.618, 2),
    }


def rsi_implied_price(
    closes: pd.Series,
    period: int = 14,
    target_rsi: float = 35.0,
) -> float | None:
    """Calcula a qué precio el RSI Wilder caería justo en `target_rsi`.

    Resuelve la ecuación inversa del RSI:
        RSI = 100 - 100/(1 + avg_gain/avg_loss)

    Para que el RSI sea exactamente `target_rsi`, sin nuevas ganancias
    (peor caso = mañana cae), necesitamos calcular qué pérdida hace que
    el ratio gain/loss caiga al nivel correcto.

    Es una aproximación de un día — útil como referencia mínima.
    Devuelve None si el cálculo no aplica (RSI ya está debajo del target,
    o no hay datos suficientes).
    """
    if len(closes) < period + 1:
        return None

    delta = closes.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]

    if avg_gain <= 0 and avg_loss <= 0:
        return None

    # Para RSI = target: rs = target / (100 - target)
    target_rs = target_rsi / (100.0 - target_rsi)

    # Aplicando un día de pérdida adicional con suavizado Wilder:
    # new_avg_gain = avg_gain * (1 - 1/period)         (sin ganancias hoy)
    # new_avg_loss = avg_loss * (1 - 1/period) + L * (1/period)
    # Resolvemos para L tal que new_gain / new_loss = target_rs:
    alpha = 1.0 / period
    new_avg_gain = avg_gain * (1 - alpha)

    if new_avg_gain <= 0:
        return None

    target_avg_loss = new_avg_gain / target_rs
    loss_required = (target_avg_loss - avg_loss * (1 - alpha)) / alpha

    if loss_required <= 0:
        # Ya estamos por debajo del target — no se necesita más caída
        return float(closes.iloc[-1])

    implied_price = float(closes.iloc[-1]) - float(loss_required)
    return max(implied_price, 0.0)  # nunca negativo


def compute_price_targets(
    closes: pd.Series,
    rsi_target: float = 35.0,
) -> PriceTargets:
    """Combina los tres métodos y produce un único precio objetivo + recomendación.

    Args:
        closes: serie de precios de cierre (idealmente 1 año).
        rsi_target: umbral de RSI para considerar "buena entrada".

    Returns:
        PriceTargets con todos los niveles y la recomendación final.
    """
    current = float(closes.iloc[-1])
    high_52w = float(closes.max())
    low_52w = float(closes.min())
    pct_from_high = ((high_52w - current) / high_52w) * 100.0

    supports = find_pivot_lows(closes, window=10, max_results=5)
    fib = fibonacci_retracements(high_52w, low_52w)
    rsi_price = rsi_implied_price(closes, target_rsi=rsi_target)

    # Candidatos: cualquier nivel **por debajo** del precio actual.
    # (Si está por encima, ya lo cruzamos — no es objetivo de compra)
    candidates: list[tuple[float, str]] = []

    for s in supports:
        if s < current:
            candidates.append((s, "soporte técnico"))

    for label, level in fib.items():
        if level < current:
            candidates.append((level, f"Fibonacci {label}"))

    if rsi_price is not None and rsi_price < current:
        candidates.append((rsi_price, f"RSI implícito ({rsi_target:.0f})"))

    if not candidates:
        # Precio actual ya es muy bajo — todos los niveles quedaron por encima.
        # En ese caso, sugerimos el precio actual.
        suggested = current
        method = "precio actual (todos los soportes ya cruzados)"
    else:
        # El más alto por debajo del precio actual = "comprar barato sin
        # esperar un crash improbable"
        candidates.sort(key=lambda x: x[0], reverse=True)
        suggested, method = candidates[0]

    distance_pct = ((current - suggested) / current) * 100.0

    # ---- Recomendación ----
    if distance_pct <= 0:
        recommendation = "COMPRAR_AHORA"
        reason = f"El precio actual (${current:.2f}) ya está en o debajo del objetivo (${suggested:.2f})."
    elif distance_pct <= 3:
        recommendation = "CERCA"
        reason = f"A solo {distance_pct:.1f}% del objetivo (${suggested:.2f}). Preparar orden."
    elif distance_pct <= 15:
        recommendation = "ESPERAR"
        reason = f"Esperar caída a ${suggested:.2f} ({distance_pct:.1f}% abajo)."
    else:
        recommendation = "CARO"
        reason = f"Muy lejos del objetivo (-{distance_pct:.1f}%). Esperar pullback fuerte o reevaluar."

    return PriceTargets(
        current_price=current,
        suggested_buy_price=round(suggested, 2),
        distance_pct=round(distance_pct, 2),
        supports=[round(s, 2) for s in supports],
        fibonacci_levels=fib,
        rsi_implied_price=round(rsi_price, 2) if rsi_price else None,
        high_52w=round(high_52w, 2),
        low_52w=round(low_52w, 2),
        pct_from_high=round(pct_from_high, 2),
        method_used=method,
        recommendation=recommendation,
        recommendation_reason=reason,
    )
