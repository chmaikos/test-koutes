import { afterEach, describe, expect, it, vi } from "vitest";
import { startLiveStream } from "./liveStream";

class FakeSource {
  listeners = new Map<string, (event: unknown) => void>();
  close = vi.fn();
  addEventListener(type: string, listener: (event: unknown) => void) { this.listeners.set(type, listener); }
  emit(type: string) { this.listeners.get(type)?.({ data: "{}" }); }
}

afterEach(() => vi.useRealTimers());

describe("live stream reconnect", () => {
  it("closes automatic retries and reacquires a token with backoff", async () => {
    vi.useFakeTimers();
    const sources: FakeSource[] = [];
    const urls: string[] = [];
    const acquireToken = vi.fn().mockResolvedValueOnce("expired").mockResolvedValue("fresh");
    const stop = startLiveStream({
      baseURL: "/api", acquireToken, onMessage: vi.fn(), onReauthenticate: vi.fn(),
      createSource: (url) => { urls.push(url); const source = new FakeSource(); sources.push(source); return source as unknown as EventSource; },
    });
    await vi.advanceTimersByTimeAsync(0);
    sources[0].emit("error");
    expect(sources[0].close).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(999);
    expect(acquireToken).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(urls).toEqual(["/api/stream?access_token=expired", "/api/stream?access_token=fresh"]);
    sources[1].emit("error");
    await vi.advanceTimersByTimeAsync(1999);
    expect(acquireToken).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(acquireToken).toHaveBeenCalledTimes(3);
    stop();
    await vi.advanceTimersByTimeAsync(60000);
    expect(acquireToken).toHaveBeenCalledTimes(3);
  });

  it("retries token acquisition failures and cancels an in-flight acquisition on unmount", async () => {
    vi.useFakeTimers();
    let resolveToken: (token: string) => void = () => {};
    const acquireToken = vi.fn().mockRejectedValueOnce(new Error("offline")).mockImplementationOnce(() => new Promise<string>((resolve) => { resolveToken = resolve; }));
    const createSource = vi.fn();
    const stop = startLiveStream({ baseURL: "/api", acquireToken, createSource, onMessage: vi.fn(), onReauthenticate: vi.fn() });
    await vi.advanceTimersByTimeAsync(1000);
    expect(acquireToken).toHaveBeenCalledTimes(2);
    stop();
    resolveToken("fresh");
    await vi.advanceTimersByTimeAsync(0);
    expect(createSource).not.toHaveBeenCalled();
  });

  it("refreshes account authorization when the server rejects a live session", async () => {
    vi.useFakeTimers();
    const source = new FakeSource();
    const onReauthenticate = vi.fn();
    const stop = startLiveStream({ baseURL: "/api", acquireToken: async () => "token", onMessage: vi.fn(), onReauthenticate, createSource: () => source as unknown as EventSource });
    await vi.advanceTimersByTimeAsync(0);
    source.emit("reauthenticate");
    expect(onReauthenticate).toHaveBeenCalledOnce();
    expect(source.close).toHaveBeenCalledOnce();
    stop();
  });
});
