import { useEffect, useState } from 'react';
import { getJSON } from './api';

export default function useRemote<T>(path: string | null) {
  const [state, setState] = useState<{ path: string | null; data: T | null; error: string; loading: boolean }>({ path: null, data: null, error: '', loading: false });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!path) { setState({ path, data: null, error: '', loading: false }); return; }
    const controller = new AbortController();
    setState({ path, data: null, error: '', loading: true });
    getJSON<T>(path, controller.signal).then(data => { if (!controller.signal.aborted) setState({ path, data, error: '', loading: false }); })
      .catch(error => { if (!controller.signal.aborted) setState({ path, data: null, error: error.message, loading: false }); });
    return () => controller.abort();
  }, [path, attempt]);
  return { ...(state.path === path ? state : { data: null, error: '', loading: Boolean(path) }), retry: () => setAttempt(n => n + 1) };
}
