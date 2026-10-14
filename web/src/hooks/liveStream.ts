interface Options {
  acquireToken: () => Promise<string | null>;
  onMessage: (event: MessageEvent<string>) => void;
  onReauthenticate: () => void;
  baseURL: string;
  createSource?: (url: string) => EventSource;
}

// Own reconnects so every attempt obtains a current token instead of letting
// EventSource retry its original (possibly expired) token-bearing URL.
export function startLiveStream(options: Options): () => void {
  let source: EventSource | null = null;
  let stopped = false;
  let attempts = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const retry = () => {
    source?.close();
    source = null;
    if (stopped || timer !== undefined) return;
    const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempts++, 5));
    timer = setTimeout(() => { timer = undefined; void connect(); }, delay);
  };
  const connect = async () => {
    try {
      const token = await options.acquireToken();
      if (stopped) return;
      if (!token) { retry(); return; }
      source = (options.createSource ?? ((url) => new EventSource(url)))(
        `${options.baseURL}/stream?access_token=${encodeURIComponent(token)}`,
      );
      source.addEventListener("ready", () => { attempts = 0; });
      source.addEventListener("message", (event) => {
        if (!stopped) options.onMessage(event as MessageEvent<string>);
      });
      source.addEventListener("error", retry);
      source.addEventListener("reconnect", retry);
      source.addEventListener("reauthenticate", () => {
        options.onReauthenticate();
        retry();
      });
    } catch {
      if (!stopped) retry();
    }
  };
  void connect();
  return () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
    source?.close();
  };
}
