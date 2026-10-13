/*
 * First-party analytics and source attribution.
 *
 * Loaded on every page - the regular pages and the Game Boy. Answers three
 * questions: where did this visitor come from, what did they look at, and (via
 * the checkout) what did they buy.
 *
 * Deliberately minimal: no cookies, no IP address, no fingerprinting. The only
 * thing linking one event to another is a random id that lives for one tab.
 *
 * Source, in priority order:
 *   1. ?ref=  on the URL          (links you post: ?ref=tiktok, ?ref=card)
 *   2. ?utm_source=               (in case a tool adds UTM tags)
 *   3. the referring site          (google, reddit, instagram... detected)
 *   4. nothing                     -> "direct"
 *
 * The FIRST source seen in 30 days is what found the visitor and is what the
 * order is credited to. The most recent one is kept alongside it.
 */
(function () {
    'use strict';

    var SRC_KEY = 'gbr_src';
    var SID_KEY = 'gbr_sid';
    var FIRST_TOUCH_DAYS = 30;

    var script = document.currentScript;
    var SURFACE = (script && script.dataset.surface) || 'web';

    var REFERRERS = [
        [/(^|\.)google\./, 'google'],
        [/(^|\.)bing\.com$/, 'bing'],
        [/(^|\.)duckduckgo\.com$/, 'duckduckgo'],
        [/(^|\.)yahoo\./, 'yahoo'],
        [/(^|\.)(facebook\.com|fb\.com|fb\.me)$/, 'facebook'],
        [/(^|\.)instagram\.com$/, 'instagram'],
        [/(^|\.)(t\.co|twitter\.com|x\.com)$/, 'twitter'],
        [/(^|\.)reddit\.com$/, 'reddit'],
        [/(^|\.)tiktok\.com$/, 'tiktok'],
        [/(^|\.)(youtube\.com|youtu\.be)$/, 'youtube'],
        [/(^|\.)news\.ycombinator\.com$/, 'hackernews'],
        [/(^|\.)mercari\.com$/, 'mercari'],
        [/(^|\.)offerup\.com$/, 'offerup'],
        [/(^|\.)discord(app)?\.com$/, 'discord']
    ];

    function store(area) {
        try { return window[area]; } catch (e) { return null; }
    }

    function clean(value) {
        if (!value) return null;
        value = String(value).trim().toLowerCase().slice(0, 60);
        return /^[a-z0-9][a-z0-9_.:-]*$/.test(value) ? value : null;
    }

    function sessionId() {
        var ss = store('sessionStorage');
        var id = ss && ss.getItem(SID_KEY);
        if (!id) {
            id = '';
            var chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
            for (var i = 0; i < 24; i++) {
                id += chars.charAt(Math.floor(Math.random() * chars.length));
            }
            if (ss) { try { ss.setItem(SID_KEY, id); } catch (e) { /* private mode */ } }
        }
        return id;
    }

    function fromReferrer() {
        if (!document.referrer) return null;
        var host;
        try { host = new URL(document.referrer).hostname.toLowerCase(); }
        catch (e) { return null; }
        if (!host || host === location.hostname) return null;   // internal click
        for (var i = 0; i < REFERRERS.length; i++) {
            if (REFERRERS[i][0].test(host)) return REFERRERS[i][1];
        }
        return clean('referral:' + host.replace(/^www\./, ''));
    }

    function readSource() {
        var ls = store('localStorage');
        try { return JSON.parse((ls && ls.getItem(SRC_KEY)) || 'null'); }
        catch (e) { return null; }
    }

    function captureSource() {
        var params = new URLSearchParams(location.search);
        var incoming = clean(params.get('ref')) || clean(params.get('utm_source'))
                       || fromReferrer();

        // Strip the tracking params from the address bar, so a visitor who
        // copies the link to share it does not carry our attribution with it.
        if (params.has('ref') || params.has('utm_source') || params.has('utm_medium')
                || params.has('utm_campaign')) {
            ['ref', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term']
                .forEach(function (k) { params.delete(k); });
            var qs = params.toString();
            try {
                history.replaceState(null, '',
                    location.pathname + (qs ? '?' + qs : '') + location.hash);
            } catch (e) { /* ignore */ }
        }

        if (!incoming) return;

        var now = Date.now();
        var src = readSource() || {};
        var expired = !src.first || (now - (src.first_at || 0)) > FIRST_TOUCH_DAYS * 864e5;
        if (expired) {
            src.first = incoming;
            src.first_at = now;
        }
        src.last = incoming;
        src.last_at = now;

        var ls = store('localStorage');
        if (ls) { try { ls.setItem(SRC_KEY, JSON.stringify(src)); } catch (e) { /* full */ } }
    }

    function currentRef() {
        var src = readSource() || {};
        var first = src.first || 'direct';
        return { ref: first, last_ref: src.last || first };
    }

    function send(payload) {
        var body = JSON.stringify(payload);
        try {
            if (navigator.sendBeacon &&
                navigator.sendBeacon('/api/event',
                    new Blob([body], { type: 'application/json' }))) {
                return;
            }
        } catch (e) { /* fall through to fetch */ }
        try {
            fetch('/api/event', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: body,
                keepalive: true
            }).catch(function () {});
        } catch (e) { /* analytics must never break the page */ }
    }

    function track(type, extra) {
        var payload = {
            type: type,
            path: location.pathname,
            ref: currentRef().ref,
            session_id: sessionId(),
            surface: SURFACE
        };
        if (extra) {
            for (var k in extra) {
                if (Object.prototype.hasOwnProperty.call(extra, k)) payload[k] = extra[k];
            }
        }
        send(payload);

        // Mirror the interesting events into GA4 while it is still on the site.
        if (type !== 'pageview' && typeof window.gtag === 'function') {
            try { window.gtag('event', type, extra || {}); } catch (e) { /* ignore */ }
        }
    }

    captureSource();

    window.gbrTrack = track;
    window.gbrRef = currentRef;

    track('pageview');

    // Any element with data-cta="name" is counted when clicked.
    document.addEventListener('click', function (e) {
        var el = e.target.closest && e.target.closest('[data-cta]');
        if (el) track('cta', { detail: el.getAttribute('data-cta') });
    }, true);
})();
