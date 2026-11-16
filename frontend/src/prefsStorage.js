// The user's preferences (language and accessibility options), kept in this browser.
// Storage can be missing or blocked (private mode, previews): the app works without it.

const KEY = 'prefs:v1'

export function readPrefs() {
  try {
    return JSON.parse(localStorage.getItem(KEY)) ?? {}
  } catch {
    return {}
  }
}

export function savePrefs(prefs) {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs))
  } catch {
    // not saved: the choice lasts until the page is closed
  }
}
