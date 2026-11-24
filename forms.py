"""Fill a supported employer-authorized applicant form using exact known answers."""
import re
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright


class PreparedForm:
    def __init__(self, runtime, browser, page, button):
        self.runtime,self.browser,self.page,self.button=runtime,browser,page,button

    def close(self):
        self.browser.close()
        self.runtime.stop()

    def submit(self):
        self.button.click(timeout=8000)
        self.page.get_by_text(re.compile(r"^(?:thank you for applying|your application has been submitted|application submitted)[!.]?$",re.I)).first.wait_for(timeout=12000)


def prepare(url,origin,cfg,pdf,letter):
    def trusted(value):
        parts=urlsplit(value)
        return parts.scheme=="https" and parts.netloc==origin and not parts.username
    if not trusted(url):
        raise ValueError("Unsupported application origin")
    runtime=sync_playwright().start()
    browser=runtime.chromium.launch()
    try:
        page=browser.new_page()
        # Restrict navigation and form transmission to the configured employer's ATS.
        def guard(route):
            req=route.request
            if (req.is_navigation_request() or req.method not in ("GET","HEAD","OPTIONS")) and not trusted(req.url):
                route.abort()
            else:
                route.continue_()
        page.route("**/*",guard)
        page.goto(url,wait_until="domcontentloaded",timeout=25000)
        form=page.locator("form").filter(has=page.locator('input[type=file]'))
        if form.count()!=1 or not trusted(page.url):
            raise ValueError("Unsupported form")
        if page.locator('[class*=captcha i],[id*=captcha i],iframe[src*=captcha i]').count():
            raise ValueError("Human verification")
        answers={**cfg.get("answers",{}),"email":cfg["applicant_email"],"full name":cfg["name"],"cover letter":letter}
        for el in form.locator("input,select,textarea").all():
            typ=el.get_attribute("type") or ""
            if typ=="hidden" or typ=="submit" or el.is_disabled():
                continue
            label=el.evaluate("e => (Array.from(e.labels || []).map(l=>l.innerText).join(' ') || e.getAttribute('aria-label') || '').trim()")
            label=re.sub(r"\s+"," ",label.strip(" *")).lower()
            if typ=="file" and re.fullmatch(r"resume|cv|resume/cv",label):
                el.set_input_files(str(pdf))
            elif label in answers and typ not in ("checkbox","radio","file","password"):
                if el.evaluate("e => e.tagName")=="SELECT":
                    el.select_option(label=answers[label])
                else:
                    el.fill(answers[label])
            elif el.evaluate("e => e.required || e.getAttribute('aria-required')==='true'") or "*" in (el.get_attribute("aria-label") or ""):
                raise ValueError("Unanswered required question")
        button=form.get_by_role("button",name=re.compile("^submit application$",re.I))
        if button.count()!=1 or not form.evaluate("f=>f.checkValidity()"):
            raise ValueError("Incomplete application")
        return PreparedForm(runtime,browser,page,button)
    except Exception:
        browser.close()
        runtime.stop()
        raise
