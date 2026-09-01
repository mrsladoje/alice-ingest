import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import WS, launch

LANE = os.environ.get("SHIFTER_URL", "http://127.0.0.1:8092")
PROFILE = tempfile.mkdtemp(prefix="shifter-check-")

_results = []


def check(name, got, want):
    ok = got == want
    _results.append((ok, name, got, want))
    print("  %s %-34s %s" % ("PASS" if ok else "FAIL", name,
                             got if ok else "%r, wanted %r" % (got, want)))
    return ok


class Page(object):
    def __init__(self, ws):
        self.ws = ws

    def js(self, expression):
        return self.ws.js(expression)

    def wait(self, expression, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.ws.js(expression):
                return True
            time.sleep(0.1)
        return False

    def click(self, selector, at=None):
        where = "r.left + r.width / 2" if at is None else "r.left + r.width * %s" % at
        box = self.ws.js(
            "(function(){var n=document.querySelector('%s');if(!n)return null;"
            "var r=n.getBoundingClientRect();"
            "return Math.round(%s)+','+Math.round(r.top+r.height/2);})()"
            % (selector, where))
        if not box:
            return False
        x, y = [int(v) for v in box.split(",")]
        for kind in ("mousePressed", "mouseReleased"):
            self.ws.call("Input.dispatchMouseEvent", {
                "type": kind, "x": x, "y": y, "button": "left",
                "clickCount": 1})
        time.sleep(0.45)
        return True

    def key(self, name, code):
        for kind in ("keyDown", "keyUp"):
            self.ws.call("Input.dispatchKeyEvent", {
                "type": kind, "key": name, "code": name,
                "windowsVirtualKeyCode": code})
        time.sleep(0.45)

    def pick(self, index, value):
        self.ws.js(
            "(function(){var s=document.querySelectorAll('.fg-select')[%d];"
            "s.value='%s';s.dispatchEvent(new Event('change',{bubbles:true}));})()"
            % (index, value))
        time.sleep(0.45)

    def type_into(self, selector, index, value):
        self.ws.js(
            "(function(){var n=document.querySelectorAll('%s')[%d];n.value='%s';"
            "n.dispatchEvent(new Event('input',{bubbles:true}));})()"
            % (selector, index, value))
        time.sleep(0.45)

    def query(self):
        self.ws.js("document.querySelector('.btn.primary').click()")
        self.wait("document.querySelector('.statusbar')", 10)
        time.sleep(1.6)

    def status(self):
        return self.js("document.querySelector('.statusbar')"
                       ".innerText.split('\\n')[0]")

    def sql(self):
        return self.js("var n=document.querySelector('.sql');"
                       "return n?n.innerText:''".replace("return ", ""))

    def viewport(self, width, height):
        self.ws.call("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height, "deviceScaleFactor": 1,
            "mobile": width <= 640})
        time.sleep(0.6)

    def unviewport(self):
        self.ws.call("Emulation.clearDeviceMetricsOverride", {})
        time.sleep(0.6)

    def click_row(self, pane, margin=260):
        box = self.js(
            "(function(){var rows=document.querySelectorAll('%s .trow');"
            "var box=document.querySelector('%s .logtable');"
            "if(!box)return null;var pr=box.getBoundingClientRect();"
            "for(var i=0;i<rows.length;i++){"
            "var r=rows[i].getBoundingClientRect();"
            "if(r.top>pr.top+4&&r.bottom<pr.bottom-%d)"
            "return Math.round(r.left+30)+','+Math.round(r.top+r.height/2);}"
            "return null;})()" % (pane, pane, margin))
        if not box:
            return False
        x, y = [int(v) for v in box.split(",")]
        for kind in ("mousePressed", "mouseReleased"):
            self.ws.call("Input.dispatchMouseEvent", {
                "type": kind, "x": x, "y": y, "button": "left",
                "clickCount": 1})
        time.sleep(0.5)
        return True

    def click_last_row(self, pane):
        box = self.js(
            "(function(){var rows=document.querySelectorAll('%s .trow');"
            "var b=document.querySelector('%s .logtable');"
            "if(!b)return null;var pr=b.getBoundingClientRect();var hit=null;"
            "for(var i=0;i<rows.length;i++){"
            "var r=rows[i].getBoundingClientRect();"
            "if(r.top>pr.top+4&&r.bottom<pr.bottom-2)"
            "hit=Math.round(r.left+30)+','+Math.round(r.top+r.height/2);}"
            "return hit;})()" % (pane, pane))
        if not box:
            return False
        x, y = [int(v) for v in box.split(",")]
        for kind in ("mousePressed", "mouseReleased"):
            self.ws.call("Input.dispatchMouseEvent", {
                "type": kind, "x": x, "y": y, "button": "left",
                "clickCount": 1})
        time.sleep(0.5)
        return True

    def detail_button(self):
        return self.js("var n=document.querySelector('.btn.detailmode');"
                       "n?n.textContent:null")

    def press_detail_button(self):
        self.ws.js("document.querySelector('.btn.detailmode').click()")
        time.sleep(0.5)

    def reload(self):
        self.ws.call("Page.navigate", {"url": LANE + "/"})
        self.wait("!!document.querySelector('.filtergrid')", 20)
        time.sleep(1.2)

    def goto(self, hash_part):
        self.ws.call("Page.navigate", {"url": "about:blank"})
        time.sleep(0.5)
        self.ws.call("Page.navigate", {"url": LANE + "/#" + hash_part})
        self.wait("!!document.querySelector('.filtergrid')", 20)
        time.sleep(1.2)
        self.arm_copy()

    def arm_copy(self):
        self.ws.js(
            "window.__prompted=false;"
            "window.prompt=function(){window.__prompted=true;return null;};"
            "window.__copied=null;"
            "Object.defineProperty(navigator,'clipboard',{configurable:true,"
            "value:{writeText:function(t){window.__copied=t;"
            "return Promise.resolve();}}});")

    def blind_copy(self):
        self.ws.js(
            "window.__prompted=false;"
            "window.prompt=function(){window.__prompted=true;return null;};"
            "Object.defineProperty(navigator,'clipboard',{configurable:true,"
            "value:undefined});")

    def open_saved(self):
        self.ws.js("Array.from(document.querySelectorAll('.toolbar .dd .btn'))"
                   ".filter(function(b){return /Saved/.test(b.textContent)"
                   "})[0].click()")
        time.sleep(0.5)

    def preset_link(self, name):
        self.ws.js(
            "(function(){var rows=document.querySelectorAll('.preset');"
            "for(var i=0;i<rows.length;i++){"
            "var n=rows[i].querySelector('.preset-name');"
            "if(n&&n.textContent==='%s'){"
            "rows[i].querySelector('.preset-link').click();return;}}})()"
            % name)
        time.sleep(0.5)
        return self.js("(window.__copied||'').split('#')[1]")


def first_load(page):
    print("\nfirst load")
    check("range picker default", page.js(
        "document.querySelectorAll('.fg-select')[0].value"), "1h")
    check("exclude picker default", page.js(
        "document.querySelectorAll('.fg-select')[1].value"), "none")
    check("idle placeholder", page.js(
        "var n=document.querySelector('.empty');n?n.textContent:null"),
        "Set filters above, then press Query or hit Enter.")
    check("favicon is linked", page.js(
        "var l=document.querySelector('link[rel=icon]');"
        "l?l.getAttribute('href'):null"), "alice-favicon.svg")


def ranges(page):
    print("\ntime ranges")
    check("preset list", page.js(
        "Array.from(document.querySelectorAll('.fg-select')[0].options)"
        ".map(function(o){return o.value}).join(',')"),
        "15m,1h,6h,24h,7d,exact")
    page.pick(0, "24h")
    page.query()
    check("24h returns a page", page.js(
        "document.querySelectorAll('.querypane .trow').length > 0"), True)
    check("24h count is exact", page.js(
        "/of [0-9,]+ matched/.test(document.querySelector('.statusbar')"
        ".innerText) && !/\\+ matched/.test(document.querySelector"
        "('.statusbar').innerText)"), True)


def exact_and_formats(page):
    print("\nexact range, CERN dates, format menu")
    page.pick(0, "exact")
    check("between row appears", page.js(
        "!!document.querySelector('.fg-exact')"), True)
    check("placeholder is CERN", page.js(
        "document.querySelector('.fg-when').placeholder"), "dd.mm.yyyy hh:mm")
    check("format menu default", page.js(
        "document.querySelector('.fg-fmt').value"), "cern")
    check("exactly one format menu", page.js(
        "document.querySelectorAll('.fg-fmt').length"), 1)

    page.type_into(".fg-when", 0, "31.08.2026 09:00")
    page.query()
    check("local 09:00 is sent as UTC", page.js(
        "/@timestamp >= 2026-08-31T07:00:00.000Z/.test("
        "document.querySelector('.sql').innerText)"), True)

    for key, want in (("iso", "2026-08-31 09:00"),
                      ("us", "08/31/2026 09:00"),
                      ("cern", "31.08.2026 09:00")):
        page.ws.js(
            "(function(){var s=document.querySelector('.fg-fmt');s.value='%s';"
            "s.dispatchEvent(new Event('change',{bubbles:true}));})()" % key)
        time.sleep(0.45)
        check("format %s rewrites the box" % key,
              page.js("document.querySelector('.fg-when').value"), want)

    page.type_into(".fg-when", 0, "31.13.2026")
    check("bad date is marked", page.js(
        "document.querySelector('.fg-when').className.indexOf('bad') >= 0"),
        True)
    check("bad date blocks Query", page.js(
        "document.querySelector('.btn.primary').disabled"), True)
    page.type_into(".fg-when", 0, "31.08.2026 09:00")
    check("Query is released again", page.js(
        "document.querySelector('.btn.primary').disabled"), False)


def calendar(page):
    print("\ncalendar")
    check("starts closed", page.js("!!document.querySelector('.cal')"), False)
    page.click(".fg-cal")
    check("button opens it", page.js("!!document.querySelector('.cal')"), True)
    page.click(".fg-cal")
    check("button closes it", page.js("!!document.querySelector('.cal')"),
          False)
    page.click(".fg-when", 0.2)
    check("clicking the date opens it", page.js(
        "!!document.querySelector('.cal')"), True)
    page.click(".thead")
    check("clicking outside closes it", page.js(
        "!!document.querySelector('.cal')"), False)
    page.click(".fg-when", 0.2)
    page.key("Escape", 27)
    check("Escape closes it", page.js("!!document.querySelector('.cal')"),
          False)
    page.click(".fg-when", 0.2)
    page.ws.js("(function(){var d=Array.from(document.querySelectorAll"
               "('.cal-day')).filter(function(b){return b.textContent==='15'});"
               "d[0]&&d[0].click();})()")
    time.sleep(0.5)
    check("picking a day sets the box", page.js(
        "/^15\\.[0-9]{2}\\.[0-9]{4} /.test("
        "document.querySelector('.fg-when').value)"), True)
    check("picking a day closes it", page.js(
        "!!document.querySelector('.cal')"), False)
    page.click(".fg-when", 0.2)
    page.type_into(".cal-clock", 0, "09")
    check("the clock stays open", page.js(
        "!!document.querySelector('.cal')"), True)
    check("the clock writes the hour", page.js(
        "/ 09:/.test(document.querySelector('.fg-when').value)"), True)
    page.click(".fg-when", 0.8)
    check("clicking the time closes it", page.js(
        "!!document.querySelector('.cal')"), False)


def counting(page):
    print("\ncounting and the exclude window")
    page.type_into(".fg-when", 0, "")
    page.type_into(".fg-when", 1, "")
    page.query()
    check("no start date gives a floor", page.js(
        "/\\+ matched/.test(document.querySelector('.statusbar').innerText)"),
        True)
    page.pick(1, "hide")
    check("except row appears", page.js(
        "Array.from(document.querySelectorAll('.fg-exact .fg-rowlabel'))"
        ".map(function(n){return n.textContent}).join('+')"), "between+except")
    page.type_into(".fg-when", 2, "31.08.2026 13:00")
    page.type_into(".fg-when", 3, "31.08.2026 13:30")
    page.query()
    check("exclude becomes a NOT range", page.js(
        "/NOT \\(@timestamp >= [^)]+ AND @timestamp <= [^)]+\\)/.test("
        "document.querySelector('.sql').innerText)"), True)


def paging_and_clear(page):
    print("\npaging, then Clear")
    page.pick(1, "none")
    page.pick(0, "1h")
    page.query()
    first = page.js("document.querySelector('.statusbar').innerText"
                    ".match(/^([0-9,]+) of/)[1]")
    check("first page is 500 rows", first, "500")
    page.ws.js("var p=document.querySelector('.querypane .vscroll')"
               "||document.querySelector('.querypane .tbody');"
               "if(p){p.scrollTop=0;}")
    time.sleep(2.5)
    grew = page.js("parseInt(document.querySelector('.statusbar').innerText"
                   ".match(/^([0-9,]+) of/)[1].replace(/,/g,''),10) >= 500")
    check("scrolling up keeps or grows the page", grew, True)

    page.ws.js("Array.from(document.querySelectorAll('.btn')).filter("
               "function(b){return b.textContent.trim()==='Clear'})[0].click()")
    time.sleep(0.8)
    check("Clear restores the last hour", page.js(
        "document.querySelectorAll('.fg-select')[0].value"), "1h")
    check("Clear turns the exclude off", page.js(
        "document.querySelectorAll('.fg-select')[1].value"), "none")
    check("Clear empties the result", page.js(
        "document.querySelectorAll('.querypane .trow').length"), 0)


def jump_arrows(page):
    print("\nerror jump arrows")
    page.pick(0, "24h")
    page.query()
    alerts = page.js(
        "document.querySelectorAll('.querypane .trow.sev-error,"
        ".querypane .trow.sev-fatal').length")
    rows = page.js("document.querySelectorAll('.querypane .trow').length")
    print("      (%s alert rows rendered of %s)" % (alerts, rows))

    def picked():
        return page.js(
            "var n=document.querySelector('.querypane .trow.selected');"
            "n?n.style.top+'|'+n.innerText.slice(0,40):null")

    def is_alert():
        return page.js(
            "var n=document.querySelector('.querypane .trow.selected');"
            "return n ? /sev-(error|fatal)/.test(n.className) : null"
            .replace("return ", ""))

    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='|\u25c0'})[0].click()")
    time.sleep(0.7)
    first = picked()
    check("|< selects an alert", is_alert(), True)

    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='\u25b6'})[0].click()")
    time.sleep(0.7)
    nxt = picked()
    check("> moves to another alert", nxt is not None and nxt != first, True)
    check("> lands on an alert", is_alert(), True)

    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='\u25c0'})[0].click()")
    time.sleep(0.7)
    check("< goes back to the first", picked(), first)

    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='\u25b6|'})[0].click()")
    time.sleep(0.7)
    last = picked()
    check(">| selects an alert", is_alert(), True)
    check(">| is not the first one", last != first, True)

    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='\u25bc'})[0].click()")
    time.sleep(0.7)
    check("v goes to the newest row", page.js(
        "var t=document.querySelectorAll('.querypane .trow');"
        "var n=document.querySelector('.querypane .trow.selected');"
        "return !!n && t[t.length-1]===n".replace("return ", "")), True)


def toolbar(page):
    print("\ntoolbar — every control the refactor moved")

    def btn(label):
        return ("Array.from(document.querySelectorAll('.toolbar .btn'))"
                ".filter(function(b){return b.textContent.trim()==='%s'})[0]"
                % label)

    page.pick(0, "1h")
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .chip'))"
               ".filter(function(c){return c.textContent==='info'})[0].click()")
    time.sleep(0.5)
    check("a severity chip turns off", page.js(
        "Array.from(document.querySelectorAll('.toolbar .chip')).filter("
        "function(c){return c.textContent==='info'})[0]"
        ".className.indexOf(' on') >= 0"), False)
    page.query()
    check("the chip leaves info out of the query", page.js(
        "/severity_norm in \\(([^)]*)\\)/.exec(document.querySelector("
        "'.sql').innerText)[1].indexOf('info') === -1"), True)
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .chip'))"
               ".filter(function(c){return c.textContent==='info'})[0].click()")
    time.sleep(0.5)

    page.ws.js(btn("Filters ▾").replace("'Filters ▾'", "'Filters'")
               + " && null")
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .dd .btn'))"
               ".filter(function(b){return /Filters/.test(b.textContent)})[0]"
               ".click()")
    time.sleep(0.5)
    check("the Filters menu opens", page.js(
        "document.querySelectorAll('.adv-block').length"), 2)
    page.ws.js("Array.from(document.querySelectorAll('.adv .chip')).filter("
               "function(c){return /regular expression/.test(c.textContent)"
               "})[0].click()")
    time.sleep(0.5)
    check("regex mode arms the Filters button", page.js(
        "Array.from(document.querySelectorAll('.toolbar .dd .btn')).filter("
        "function(b){return /Filters/.test(b.textContent)})[0]"
        ".textContent.indexOf('regex') >= 0"), True)
    page.ws.js("Array.from(document.querySelectorAll('.adv .chip')).filter("
               "function(c){return /substring/.test(c.textContent)})[0].click()")
    time.sleep(0.5)
    page.click(".thead")

    page.ws.js("Array.from(document.querySelectorAll('.toolbar .dd .btn'))"
               ".filter(function(b){return /Saved/.test(b.textContent)})[0]"
               ".click()")
    time.sleep(0.5)
    check("the Saved menu opens", page.js(
        "document.querySelectorAll('.preset-name').length > 0"), True)
    before = page.js("document.querySelectorAll('.preset-name').length")
    page.type_into(".preset-save input", 0, "check-preset")
    check("the Save button unlocks", page.js(
        "document.querySelector('.preset-save button').disabled"), False)
    page.ws.js("document.querySelector('.preset-save button').click()")
    time.sleep(0.9)
    check("saving adds a preset", page.js(
        "document.querySelectorAll('.preset-name').length") > before, True)
    check("the preset reached localStorage", page.js(
        "(localStorage.getItem('alice.shifter.presets.v1')||'')"
        ".indexOf('check-preset') >= 0"), True)
    page.ws.js("(function(){var rows=Array.from(document.querySelectorAll("
               "'.preset'));for(var i=0;i<rows.length;i++){"
               "if(/check-preset/.test(rows[i].textContent)){var b="
               "rows[i].querySelector('.preset-del');"
               "b&&b.click();return;}}})()")
    time.sleep(0.7)
    check("removing takes it out of localStorage", page.js(
        "(localStorage.getItem('alice.shifter.presets.v1')||'')"
        ".indexOf('check-preset') >= 0"), False)
    page.click(".thead")

    for want in ("half", "full", "collapsed"):
        page.ws.js(btn("Live lane") + ".click()")
        time.sleep(0.5)
        check("Live lane cycles to " + want, page.js(
            "var d=document.querySelector('.dock');"
            "d?d.className.replace('dock','').trim():null"), want)

    page.ws.js(btn("Inspector") + ".click()")
    time.sleep(0.5)
    check("Inspector hides", page.js(
        "!!document.querySelector('.inspector')"), False)
    page.ws.js(btn("Inspector") + ".click()")
    time.sleep(0.5)
    check("Inspector comes back", page.js(
        "!!document.querySelector('.inspector')"), True)

    check("the Cockpit link points somewhere", page.js(
        "var a=document.querySelector('.toolbar a.btn.link');"
        "!!(a && a.getAttribute('href'))"), True)


def few_rows_jump(page):
    print("\nprevious-error arrow when the result is shorter than the window")
    page.pick(0, "15m")
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .chip'))"
               ".forEach(function(c){var on=c.className.indexOf(' on')>=0;"
               "var keep=c.textContent==='fatal';if(on!==keep)c.click();})")
    time.sleep(0.6)
    page.query()
    rows = page.js("document.querySelectorAll('.querypane .trow').length")
    pane = page.js("var p=document.querySelector('.querypane .tbody')||"
                   "document.querySelector('.querypane');"
                   "p?Math.round(p.clientHeight):0")
    print("      (%s rows in a %spx pane)" % (rows, pane))
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='\u25c0'})[0].click()")
    time.sleep(0.7)
    check("prev still selects a row", page.js(
        "!!document.querySelector('.querypane .trow.selected')"), True)
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .chip'))"
               ".forEach(function(c){if(c.className.indexOf(' on')<0)c.click();})")
    time.sleep(0.6)


def row_detail(page):
    print("\nrecord details under the row")
    page.pick(0, "1h")
    page.query()
    check("desktop starts on the side panel", page.detail_button(),
          "Side panel")
    check("clicking a row fills the side inspector", page.click_row(".querypane")
          and page.js("document.querySelectorAll('.inspector .kv').length") > 8,
          True)
    check("the inspector leads with the event time", page.js(
        "var n=document.querySelector('.inspector .kv .k');"
        "n?n.textContent:null"), "event time")
    check("the rest of its keys stay sorted", page.js(
        "(function(){var k=Array.from("
        "document.querySelectorAll('.inspector .kv .k'))"
        ".map(function(n){return n.textContent}).slice(1);"
        "return JSON.stringify(k)===JSON.stringify(k.slice().sort());})()"),
          True)
    check("the inspector never shows the internal id", page.js(
        "Array.from(document.querySelectorAll('.inspector .kv .k'))"
        ".every(function(n){return n.textContent!=='_id'})"), True)
    page.key("Escape", 27)
    check("Escape leaves a side-panel selection alone", page.js(
        "!!document.querySelector('.querypane .trow.selected')"), True)
    page.ws.js("document.querySelectorAll('.fg-input')[1].focus()")
    time.sleep(0.3)
    page.key("Escape", 27)
    check("Escape in a filter box leaves it alone too", page.js(
        "!!document.querySelector('.querypane .trow.selected')"), True)
    page.press_detail_button()
    check("the button switches to under row", page.detail_button(),
          "Under row")
    check("the record already selected moves under its row", page.js(
        "!!document.querySelector('.querypane .rowdetail')"), True)
    check("the panel sits inside the scroller", page.js(
        "!!document.querySelector('.querypane .logtable .rowdetail')"), True)
    check("the panel starts at the bottom edge of its row", page.js(
        "(function(){var d=document.querySelector('.rowdetail');"
        "var r=document.querySelector('.querypane .trow.selected');"
        "if(!d||!r)return null;"
        "return Math.abs(d.getBoundingClientRect().top-"
        "r.getBoundingClientRect().bottom)<3;})()"), True)
    check("the side inspector steps aside", page.js(
        "!!document.querySelector('.inspector')"), False)
    fields = page.js("document.querySelectorAll('.rowdetail .kv').length")
    shown = page.js("document.querySelectorAll('.thead .cell').length")
    check("the panel lists more fields than the table shows",
          fields > shown, True)
    check("it carries a column the table dropped", page.js(
        "Array.from(document.querySelectorAll('.rowdetail .kv .k'))"
        ".some(function(k){return k.textContent==='facility'})"), True)
    check("it carries the whole message", page.js(
        "!!document.querySelector('.rowdetail .insp-msg')"), True)
    page.ws.js("document.querySelector('.rowdetail .rd-head .btn').click()")
    time.sleep(0.5)
    check("Close puts the panel away", page.js(
        "!!document.querySelector('.rowdetail')"), False)
    check("clicking a row opens it", page.click_row(".querypane")
          and page.js("!!document.querySelector('.querypane .rowdetail')"), True)
    page.key("Escape", 27)
    check("Escape puts it away", page.js(
        "!!document.querySelector('.rowdetail')"), False)
    page.click_row(".querypane")
    page.click_row(".querypane")
    check("clicking the same row twice closes it", page.js(
        "!!document.querySelector('.rowdetail')"), False)
    check("the last row flips the panel above itself",
          page.click_last_row(".querypane")
          and page.js(
              "(function(){var d=document.querySelector('.rowdetail');"
              "var r=document.querySelector('.querypane .trow.selected');"
              "if(!d||!r)return null;"
              "return Math.abs(d.getBoundingClientRect().bottom-"
              "r.getBoundingClientRect().top)<3;})()"), True)
    check("the flipped panel stays inside the pane", page.js(
        "(function(){var d=document.querySelector('.rowdetail')"
        ".getBoundingClientRect();"
        "var p=document.querySelector('.querypane .logtable')"
        ".getBoundingClientRect();"
        "return d.top>=p.top-1&&d.bottom<=p.bottom+1;})()"), True)
    page.key("Escape", 27)
    page.press_detail_button()
    check("switching back brings the side inspector home", page.js(
        "!!document.querySelector('.inspector')"), True)


def phone_layout(page):
    print("\na phone-sized window")
    page.ws.js("localStorage.removeItem('alice.shifter.detail.v1')")
    page.viewport(390, 780)
    page.reload()
    columns = page.js(
        "Array.from(document.querySelectorAll('.thead .cell'))"
        ".map(function(c){return c.textContent}).join(',')")
    check("the table drops to the three that fit", columns, "S,Time,Host,Message")
    check("details default to under the row", page.detail_button(), "Under row")
    page.pick(0, "1h")
    page.query()
    check("tapping a row opens the panel", page.click_row(".querypane")
          and page.js("!!document.querySelector('.rowdetail')"), True)
    check("no 88vw drawer covers the table", page.js(
        "!!document.querySelector('.inspector')"), False)
    check("the panel reaches the full window width", page.js(
        "(function(){var d=document.querySelector('.rowdetail');"
        "return d?Math.round(d.getBoundingClientRect().width):0;})()") >= 380,
          True)
    check("the dropped columns are readable in it", page.js(
        "Array.from(document.querySelectorAll('.rowdetail .kv .k'))"
        ".map(function(k){return k.textContent}).filter(function(k){"
        "return k==='facility'||k==='run'||k==='detector'}).length"), 3)
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='Live lane'})[0]"
               ".click()")
    time.sleep(1.2)
    check("tapping a live-lane row opens the panel too",
          page.click_row(".dock", 30)
          and page.js("!!document.querySelector('.dock .rowdetail')"), True)
    check("the lane panel fits the phone dock", page.js(
        "(function(){var d=document.querySelector('.dock .rowdetail')"
        ".getBoundingClientRect();"
        "var p=document.querySelector('.dock .logtable')"
        ".getBoundingClientRect();"
        "return d.top>=p.top-1&&d.bottom<=p.bottom+1;})()"), True)
    check("its dropped columns are readable as well", page.js(
        "Array.from(document.querySelectorAll('.dock .rowdetail .kv .k'))"
        ".map(function(k){return k.textContent}).filter(function(k){"
        "return k==='facility'||k==='run'||k==='detector'}).length"), 3)
    page.ws.js("localStorage.removeItem('alice.shifter.detail.v1')")
    page.unviewport()
    page.reload()


def share_link(page):
    print("\nthe shareable link")
    page.reload()
    page.arm_copy()

    page.open_saved()
    check("a built-in preset carries a link button", page.js(
        "!!document.querySelector('.preset .preset-link')"), True)
    check("errors preset encodes only the severities",
          page.preset_link("Errors, last hour"), "sev=fatal,error")
    check("warnings preset encodes three severities",
          page.preset_link("Warnings and worse"), "sev=fatal,error,warning")
    check("a range-only preset encodes only the range",
          page.preset_link("Everything, last 15 minutes"), "range=15m")
    check("a field preset encodes only that field",
          page.preset_link("Quality Control only"), "system=QC")
    check("an exclude uses the bang key", page.preset_link(
        "Drop the known noise").split("=")[0], "message!")
    check("a preset equal to the defaults encodes to nothing",
          page.preset_link("One run"), "")

    before = page.js("(window.__copied||'').length")
    check("the whole link stays short", before < 260, True)
    print("      (%d characters, against 1,330 before)" % before)

    check("no browser dialog was raised", page.js("window.__prompted"), False)
    check("a toast confirms the copy", page.js(
        "var n=document.querySelector('.toast');n?n.textContent:null"),
        "Link copied")
    time.sleep(2.6)
    check("the toast clears itself", page.js(
        "!!document.querySelector('.toast')"), False)

    page.ws.js("document.querySelectorAll('.preset .preset-name')[5].click()")
    time.sleep(0.8)
    check("applying the empty preset does not break the page", page.js(
        "!!document.querySelector('.filtergrid')"), True)

    page.goto("program=dsgha&system=QC")
    check("a link restores the field boxes", page.js(
        "Array.from(document.querySelectorAll('.fg-input'))"
        ".map(function(n){return n.value}).filter(Boolean).join(',')"),
        "dsgha,QC")
    page.open_saved()
    page.ws.js("document.querySelector('.presets .adv-line .btn').click()")
    time.sleep(0.5)
    check("the bottom button copies what is on screen", page.js(
        "(window.__copied||'').split('#')[1]"), "program=dsgha&system=QC")

    page.goto("sev=fatal&range=6h&mode=regex&limit=1000&host=epn1%2A")
    check("a link restores the range", page.js(
        "document.querySelectorAll('.fg-select')[0].value"), "6h")
    check("a link restores regex mode", page.js(
        "/regex/.test(document.querySelector('.toolbar .dd .btn').textContent)"
        ""), True)
    check("a link restores a single severity", page.js(
        "Array.from(document.querySelectorAll('.toolbar .chip.sev-info'))[0]"
        ".className.indexOf(' on') >= 0"), False)
    check("a wildcard survives the round trip", page.js(
        "Array.from(document.querySelectorAll('.fg-input'))"
        ".map(function(n){return n.value}).filter(Boolean).join(',')"),
        "epn1*")
    page.open_saved()
    page.ws.js("document.querySelector('.presets .adv-line .btn').click()")
    time.sleep(0.5)
    check("the link round trips unchanged", page.js(
        "(window.__copied||'').split('#')[1]"),
        "sev=fatal&range=6h&mode=regex&limit=1000&host=epn1*")

    page.goto("%7B%22severities%22%3A%5B%22fatal%22%5D%7D")
    check("an old long link falls back to the defaults", page.js(
        "document.querySelectorAll('.fg-select')[0].value"), "1h")
    check("an old long link raises no error", page.js(
        "(window.__errors||[]).length"), 0)

    page.reload()
    page.blind_copy()
    page.open_saved()
    page.click(".presets .adv-line .btn")
    time.sleep(0.4)
    check("copying works without the clipboard API", page.js(
        "var n=document.querySelector('.toast');n?n.textContent:null"),
        "Link copied")
    check("still no browser dialog", page.js("window.__prompted"), False)
    page.reload()


def live_lane(page):
    print("\nlive lane")
    check("the dock is there", page.js(
        "!!document.querySelector('.dock')"), True)
    check("the stream is running", page.js(
        "var n=document.querySelector('.dock .live');"
        "n?n.textContent.toUpperCase():null"), "LIVE")
    check("rows are arriving", page.wait(
        "/[0-9]/.test(document.querySelector('.dock-count')"
        ".textContent||'')", 20), True)

    print("\nthe same record view inside the live lane")
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='Live lane'})[0]"
               ".click()")
    time.sleep(0.8)
    check("the dock opens with rows in it", page.js(
        "document.querySelectorAll('.dock .trow').length") > 0, True)
    check("clicking a lane row fills the side inspector",
          page.click_row(".dock", 30)
          and page.js("document.querySelectorAll('.inspector .kv').length") > 8,
          True)
    page.press_detail_button()
    check("the lane row opens a panel of its own", page.js(
        "!!document.querySelector('.dock .rowdetail')"), True)
    check("the panel stays inside the half-height dock", page.js(
        "(function(){var d=document.querySelector('.dock .rowdetail')"
        ".getBoundingClientRect();"
        "var p=document.querySelector('.dock .logtable')"
        ".getBoundingClientRect();"
        "return d.top>=p.top-1&&d.bottom<=p.bottom+1;})()"), True)
    check("it carries a column the lane table dropped", page.js(
        "Array.from(document.querySelectorAll('.dock .rowdetail .kv .k'))"
        ".some(function(k){return k.textContent==='facility'})"), True)
    check("only one panel is open across both tables", page.js(
        "document.querySelectorAll('.rowdetail').length"), 1)
    page.ws.js("document.querySelector('.dock .rowdetail .rd-head .btn')"
               ".click()")
    time.sleep(0.5)
    check("Close puts the lane panel away", page.js(
        "!!document.querySelector('.dock .rowdetail')"), False)
    check("a new lane row still opens it", page.click_row(".dock", 30)
          and page.js("!!document.querySelector('.dock .rowdetail')"), True)
    check("the panel survives new rows arriving", page.wait(
        "!!document.querySelector('.dock .rowdetail')", 8), True)
    page.press_detail_button()

    print("\nthe inspector when the lane fills the screen")
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='Live lane'})[0]"
               ".click()")
    time.sleep(1.0)
    check("the dock is full and the workspace is gone", page.js(
        "document.querySelector('.dock').className.indexOf('full')>=0 &&"
        "getComputedStyle(document.querySelector('.workspace')).display"
        "==='none'"), True)
    check("an inspector is still on screen", page.click_row(".dock", 30)
          and page.js(
              "Array.from(document.querySelectorAll('.inspector'))"
              ".some(function(n){var r=n.getBoundingClientRect();"
              "return r.width>0&&r.height>0;})"), True)
    check("it moved inside the dock", page.js(
        "!!document.querySelector('.dock .inspector')"), True)
    check("only one is rendered, not two", page.js(
        "document.querySelectorAll('.inspector').length"), 1)
    check("it shows the clicked record", page.js(
        "document.querySelectorAll('.dock .inspector .kv').length") > 8, True)
    check("it sits beside the lane table, not over it", page.js(
        "(function(){var i=document.querySelector('.dock .inspector')"
        ".getBoundingClientRect();"
        "var t=document.querySelector('.dock .logtable')"
        ".getBoundingClientRect();return i.left>=t.right-2;})()"), True)
    page.ws.js("document.querySelector('.dock .inspector .btn').click()")
    time.sleep(0.5)
    check("Close puts it away", page.js(
        "!!document.querySelector('.dock .inspector')"), False)
    page.ws.js("Array.from(document.querySelectorAll('.toolbar .btn'))"
               ".filter(function(b){return b.textContent==='Inspector'})[0]"
               ".click()")
    time.sleep(0.6)
    check("the Inspector button brings it back inside the dock", page.js(
        "!!document.querySelector('.dock .inspector')"), True)


def main():
    proc, wsurl = launch(LANE + "/", PROFILE)
    ws = WS(wsurl)
    for domain in ("Page", "Runtime"):
        ws.call(domain + ".enable", {})
    ws.call("Page.addScriptToEvaluateOnNewDocument", {
        "source": "window.__errors=[];"
                  "window.addEventListener('error',function(e){"
                  "window.__errors.push(String(e.message))});"
                  "window.addEventListener('unhandledrejection',function(e){"
                  "window.__errors.push('unhandled rejection: '+e.reason)});"})
    ws.call("Page.navigate", {"url": LANE + "/"})
    page = Page(ws)
    if not page.wait("!!document.querySelector('.filtergrid')", 25):
        print("the page never rendered at " + LANE)
        proc.terminate()
        return 2
    time.sleep(1.5)

    try:
        for stage in (first_load, ranges, exact_and_formats, calendar,
                      counting, paging_and_clear, jump_arrows,
                      few_rows_jump, toolbar, row_detail, phone_layout,
                      share_link, live_lane):
            stage(page)
        errors = page.js("(window.__errors||[]).slice(0, 5)")
    finally:
        proc.terminate()
        shutil.rmtree(PROFILE, ignore_errors=True)

    print("\nuncaught JavaScript errors")
    check("the page threw nothing all run", errors, [])

    failed = [r for r in _results if not r[0]]
    print("\n%d checks, %d failed" % (len(_results), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
