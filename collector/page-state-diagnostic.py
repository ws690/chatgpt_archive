import fcntl,json,re
import collector as c
from playwright.sync_api import sync_playwright
with (c.STATE/'profile.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 with sync_playwright() as p:
  engine,profile,opts=c.browser_settings(p)
  c.preserve_stale_chrome_locks(c.STATE/profile)
  ctx=engine.launch_persistent_context(str(c.STATE/profile),headless=False,viewport={'width':1280,'height':900},**opts)
  try:
   page=ctx.pages[0] if ctx.pages else ctx.new_page()
   page.goto(c.BASE+'/',wait_until='domcontentloaded',timeout=60000)
   page.wait_for_timeout(10000)
   out={'on_chatgpt':page.url.startswith(c.BASE+'/'),'on_auth_path':'/auth' in page.url or '/log-in' in page.url,'verification_title':any(x in page.title().lower() for x in ['just a moment','verify you are human'])}
   selectors={'all_chat_links':'a[href^="/c/"]','collector_chat_links':'nav a[href^="/c/"], aside a[href^="/c/"], [data-testid="history"] a[href^="/c/"]','history_id':'#history','open_sidebar':'[data-testid="open-sidebar-button"]','user_menu':'[data-testid="user-menu-button"]'}
   out.update({k:page.locator(v).count() for k,v in selectors.items()})
   out['login_buttons']=page.get_by_role('button',name=re.compile(r'^(Log in|Sign in)$',re.I)).count()
   out['login_links']=page.get_by_role('link',name=re.compile(r'^(Log in|Sign in)$',re.I)).count()
   print(json.dumps(out),flush=True)
  finally:ctx.close()
