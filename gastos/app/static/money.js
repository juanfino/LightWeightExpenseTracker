(function (global) {
  "use strict";

  const config = global.MANGOTECA_MONEY || {};
  const locale = config.locale || "es-AR";
  const defaultCurrency = config.defaultCurrency || "ARS";
  const currencies = new Map(
    (config.currencies || []).map(currency => [currency.code, currency])
  );

  function metadata(code) {
    const normalized = String(code || defaultCurrency).trim().toUpperCase();
    const currency = currencies.get(normalized);
    if (!currency) throw new RangeError(`Moneda inválida: ${code}`);
    return currency;
  }

  function formatAmount(value, code) {
    if (value === null || value === undefined) return "—";
    const currency = metadata(code);
    const number = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(number)) return "—";
    const places = Number(currency.decimal_places);
    const isWhole = Math.abs(number - Math.round(number)) < Math.pow(10, -places) / 2;
    const formatted = new Intl.NumberFormat(locale, {
      minimumFractionDigits: isWhole ? 0 : places,
      maximumFractionDigits: places,
      roundingMode: "halfExpand",
    }).format(number);
    const separator = currency.symbol === "$" ? "" : " ";
    return `${currency.symbol}${separator}${formatted}`;
  }

  function optionMarkup(selected, includeAll) {
    const selectedCode = selected || defaultCurrency;
    const options = [];
    if (includeAll) options.push('<option value="">Todas</option>');
    currencies.forEach(currency => {
      const isSelected = currency.code === selectedCode ? " selected" : "";
      options.push(`<option value="${currency.code}"${isSelected}>${currency.code}</option>`);
    });
    return options.join("");
  }

  global.MoneyFormat = Object.freeze({
    defaultCurrency,
    formatAmount,
    metadata,
    optionMarkup,
  });
})(window);
