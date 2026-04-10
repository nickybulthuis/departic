/* Departic — client-side behaviour */

// ── Collapsible calc panels ──────────────────────────────────────────────

function toggleCalc(btn, targetId) {
  var el = document.getElementById(targetId);
  var open = el.classList.toggle('is-open');
  btn.classList.toggle('is-open', open);
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

// ── Page reload (trips / countdowns) ────────────────────────────────────
// Reloads the full page on the calendar tick interval, but only when the
// tab is visible so it never interrupts an active session.

(function () {
  var secs = parseInt(document.body.dataset.pageReload, 10);
  if (!secs || secs <= 0) return;

  var reloadAt = Date.now() + secs * 1000;

  function scheduleCheck() {
    var remaining = reloadAt - Date.now();
    if (remaining <= 0) {
      if (!document.hidden) {
        window.location.reload();
      } else {
        // Tab is hidden — reload as soon as it becomes visible
        document.addEventListener('visibilitychange', function handler() {
          if (!document.hidden) {
            document.removeEventListener('visibilitychange', handler);
            window.location.reload();
          }
        });
      }
      return;
    }
    setTimeout(scheduleCheck, Math.min(remaining, 10000));
  }

  setTimeout(scheduleCheck, Math.min(secs * 1000, 10000));
})();

// ── EVCC live polling ────────────────────────────────────────────────────

(function () {
  var widget = document.getElementById('evcc-widget');
  if (!widget) return;

  var url      = widget.dataset.evccPoll;
  var interval = parseInt(widget.dataset.evccInterval, 10) || 30;

  function fmt(v, unit) {
    return v !== null && v !== undefined ? v + '\u2009' + unit : '\u2013';
  }

  function fmtPower(kw) {
    if (kw === null || kw === undefined) return '\u2013';
    var w = kw * 1000;
    if (w < 1000) return Math.round(w) + '\u2009W';
    return Math.round(kw * 10) / 10 + '\u2009kW';
  }

  function fmtTime(iso) {
    if (!iso) return '';
    try {
      var d = new Date(iso);
      var now = new Date();
      var time = d.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit', hour12: false });
      // Same day → HH:mm
      if (d.toDateString() === now.toDateString()) return time;
      // Within the next 7 days → Mon HH:mm
      if (d - now < 7 * 24 * 3600 * 1000 && d > now) {
        var dow = d.toLocaleDateString('en', { weekday: 'short' });
        return dow + ' ' + time;
      }
      // Otherwise → DD-MM HH:mm
      var dd = String(d.getDate()).padStart(2, '0');
      var mm = String(d.getMonth() + 1).padStart(2, '0');
      return dd + '-' + mm + ' ' + time;
    }
    catch (e) { return iso; }
  }

  function applyEvcc(d) {
    var pv     = d.pv_power_kw;
    var gkw    = d.grid_power_kw;
    var ckw    = d.charge_power_kw;
    var pvOn   = pv  !== null && pv  > 0;
    var gridIn = gkw !== null && gkw > 0;
    var gridEx = gkw !== null && gkw < 0;
    var carOn  = ckw !== null && ckw > 0;
    var conn   = d.vehicle_connected;
    var mode   = d.charging_mode || '';

    // ── Header: mode badge ──
    var badge = widget.querySelector('.evcc-mode-badge');
    if (badge) {
      badge.className = 'evcc-mode-badge mode-' + mode;
      badge.textContent = mode || '\u2013';
    }

    // ── Header: plan chip ──
    var chip = widget.querySelector('.evcc-plan-chip, .evcc-plan-chip-none');
    if (chip) {
      if (d.plan_soc_pct) {
        chip.className = 'evcc-plan-chip';
        chip.innerHTML = '<i class="fas fa-calendar-check"></i>' +
          d.plan_soc_pct + '\u2009% \u00b7 ' + fmtTime(d.plan_time);
      } else {
        chip.className = 'evcc-plan-chip evcc-plan-chip-none';
        chip.innerHTML = '<i class="fas fa-calendar-xmark"></i>no plan';
      }
    }

    // ── Solar source row ──
    var solRow = widget.querySelector('.ef-box-sources .ef-source:first-child');
    if (solRow) {
      solRow.className = 'ef-source' + (pvOn ? ' is-active-solar' : '');
      var sv = solRow.querySelector('.ef-source-value');
      if (sv) sv.textContent = fmtPower(pv);
    }

    // ── Grid source row ──
    var gridRow = widget.querySelector('.ef-box-sources .ef-source:last-child');
    if (gridRow) {
      gridRow.className = 'ef-source' +
        (gridIn ? ' is-active-grid' : gridEx ? ' is-active-export' : '');
      var gv = gridRow.querySelector('.ef-source-value');
      if (gv) {
        if (gkw === null || gkw === undefined) {
          gv.innerHTML = '\u2013';
        } else if (gkw < 0) {
          gv.innerHTML = '<span class="ef-col-export">' + fmtPower(-gkw) + '</span>';
        } else if (gkw > 0) {
          gv.textContent = fmtPower(gkw);
        } else {
          gv.innerHTML = '<span class="ef-col-muted">idle</span>';
        }
      }
      var gl = gridRow.querySelector('.ef-source-label');
      if (gl) gl.textContent = gridEx ? 'export' : 'grid';
    }

    // ── Left connector dot ──
    var connLeft = widget.querySelector('.ef-connector:first-of-type');
    if (connLeft) {
      var dotIn = connLeft.querySelector('.ef-connector-dot-in');
      if (pvOn || gridIn) {
        if (!dotIn) {
          dotIn = document.createElement('div');
          dotIn.className = 'ef-connector-dot ef-connector-dot-in';
          connLeft.querySelector('.ef-connector-line').after(dotIn);
        }
      } else if (dotIn) {
        dotIn.remove();
      }
    }

    // ── Charger box ──
    var charger = widget.querySelector('.ef-box-charger');
    if (charger) {
      charger.className = 'ef-box ef-box-charger' +
        (carOn ? ' is-charging' : (pvOn || gridIn) ? ' is-active' : '');
      var cv = charger.querySelector('.ef-source-value');
      if (cv) cv.textContent = fmtPower(ckw);
    }

    // ── Right connector ──
    var connRight = widget.querySelector('.ef-connector:last-of-type');
    if (connRight) {
      connRight.className = 'ef-connector' + (carOn ? '' : ' ef-connector-idle');
      var dotOut = connRight.querySelector('.ef-connector-dot-out');
      if (carOn) {
        if (!dotOut) {
          dotOut = document.createElement('div');
          dotOut.className = 'ef-connector-dot ef-connector-dot-out';
          connRight.querySelector('.ef-connector-line').after(dotOut);
        }
      } else if (dotOut) {
        dotOut.remove();
      }
    }

    // ── Car box ──
    var car = widget.querySelector('.ef-box-car');
    if (car) {
      var socOk  = d.vehicle_soc_pct !== null && d.vehicle_soc_pct !== undefined &&
                   d.plan_soc_pct && d.vehicle_soc_pct >= d.plan_soc_pct;
      var socLow = d.vehicle_soc_pct !== null && d.vehicle_soc_pct !== undefined &&
                   d.plan_soc_pct && d.vehicle_soc_pct < d.plan_soc_pct;
      var socCls = socOk  ? ' is-soc-ok' :
                   socLow ? ' is-soc-low' :
                   conn   ? ' is-connected' : '';

      car.className = 'ef-box ef-box-car' + socCls + (carOn ? ' is-charging' : '');

      // SoC → target on one line
      var carVal = car.querySelector('.ef-source-value');
      if (carVal) {
        if (d.vehicle_soc_pct !== null && d.vehicle_soc_pct !== undefined) {
          carVal.textContent = d.vehicle_soc_pct + '\u2009%' +
            (d.plan_soc_pct ? '\u2002\u2192\u2002' + d.plan_soc_pct + '\u2009%' : '');
        } else if (conn) {
          carVal.textContent = 'ready';
        } else {
          carVal.textContent = '\u2013';
        }
      }

      // Status label
      var carLbl = car.querySelector('.ef-source-label');
      if (carLbl) {
        if (carOn)     carLbl.textContent = 'charging';
        else if (conn) carLbl.textContent = 'connected';
        else           carLbl.textContent = 'disconnected';
      }
    }

    // ── Footer stats ──
    var footerStrongs = widget.querySelectorAll('.ef-footer-stat strong');
    if (footerStrongs.length >= 3) {
      footerStrongs[0].textContent = d.session_energy_kwh !== null && d.session_energy_kwh !== undefined
        ? d.session_energy_kwh + '\u2009kWh' : '\u2013';
      footerStrongs[1].textContent = d.charge_remaining_kwh
        ? d.charge_remaining_kwh + '\u2009kWh' : '\u2013';
      footerStrongs[2].textContent = d.solar_pct_30d !== null && d.solar_pct_30d !== undefined
        ? Math.round(d.solar_pct_30d) + '\u2009%' : '\u2013';
    }
  }

  function poll() {
    fetch(url)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && !d.error) applyEvcc(d); })
      .catch(function () { /* silent — stale data stays visible */ });
  }

  setInterval(poll, interval * 1000);
  // first poll fires after one full interval; page load already has fresh data
})();









