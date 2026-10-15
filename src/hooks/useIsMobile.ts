import { useEffect, useState } from 'react';

// True on a phone-sized viewport. matchMedia can be missing (older embeds) or
// stubbed (tests), so we read defensively and default to the desktop layout -
// the layout the whole existing app was built and tested against.
const QUERY = '(max-width: 640px)';

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState<boolean>(() => {
    try {
      return window.matchMedia(QUERY).matches;
    } catch {
      return false;
    }
  });

  useEffect(() => {
    let mql: MediaQueryList;
    try {
      mql = window.matchMedia(QUERY);
    } catch {
      return;
    }
    const onChange = () => setIsMobile(mql.matches);
    onChange();
    // Safari <14 only has the deprecated addListener.
    if (mql.addEventListener) mql.addEventListener('change', onChange);
    else if (mql.addListener) mql.addListener(onChange);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener('change', onChange);
      else if (mql.removeListener) mql.removeListener(onChange);
    };
  }, []);

  return isMobile;
}
