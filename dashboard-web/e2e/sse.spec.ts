import { test, expect } from "@playwright/test";
import { parseSSEFrames, parseSingleSSEFrame } from "../lib/api";
import { sanitizeNextPath } from "../app/login/page";

test.describe("SSE Parser (FE-9)", () => {
  test("parses a standard single event frame", () => {
    const raw = "event: message\ndata: {\"text\": \"hello\"}";
    const parsed = parseSingleSSEFrame(raw);
    expect(parsed).toEqual({
      event: "message",
      data: { text: "hello" },
    });
  });

  test("handles CRLF line endings", () => {
    const buffer = "event: delta\r\ndata: {\"delta\": \"chunk 1\"}\r\n\r\nevent: delta\r\ndata: {\"delta\": \"chunk 2\"}\r\n\r\n";
    const { events, remaining } = parseSSEFrames(buffer);
    expect(remaining).toBe("");
    expect(events).toHaveLength(2);
    expect(events[0]).toEqual({ event: "delta", data: { delta: "chunk 1" } });
    expect(events[1]).toEqual({ event: "delta", data: { delta: "chunk 2" } });
  });

  test("joins multi-line data lines with newline", () => {
    const raw = "event: text\ndata: first line\ndata: second line";
    const parsed = parseSingleSSEFrame(raw);
    expect(parsed).toEqual({
      event: "text",
      data: "first line\nsecond line",
    });
  });

  test("supports error events", () => {
    const raw = "event: error\ndata: {\"detail\": \"Rate limit exceeded\"}";
    const parsed = parseSingleSSEFrame(raw);
    expect(parsed).toEqual({
      event: "error",
      data: { detail: "Rate limit exceeded" },
    });
  });

  test("retains incomplete frame in remaining buffer", () => {
    const buffer = "event: start\ndata: {\"id\": 1}\n\nevent: partial\ndata: incomplete";
    const { events, remaining } = parseSSEFrames(buffer);
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ event: "start", data: { id: 1 } });
    expect(remaining).toBe("event: partial\ndata: incomplete");
  });

  test("falls back gracefully when data is not valid JSON", () => {
    const raw = "event: raw\ndata: this is plain text not json";
    const parsed = parseSingleSSEFrame(raw);
    expect(parsed).toEqual({
      event: "raw",
      data: "this is plain text not json",
    });
  });

  test("ignores comment lines starting with colon", () => {
    const raw = ": this is a comment\nevent: ping\ndata: pong";
    const parsed = parseSingleSSEFrame(raw);
    expect(parsed).toEqual({
      event: "ping",
      data: "pong",
    });
  });
});

test.describe("Login next-path sanitization (SEC-16)", () => {
  test("allows valid relative internal paths", () => {
    expect(sanitizeNextPath("/app")).toBe("/app");
    expect(sanitizeNextPath("/app/library?q=test")).toBe("/app/library?q=test");
    expect(sanitizeNextPath("/app/settings")).toBe("/app/settings");
  });

  test("rejects external open redirect attempts", () => {
    expect(sanitizeNextPath("https://evil.example.com")).toBe("/app");
    expect(sanitizeNextPath("http://evil.example.com")).toBe("/app");
    expect(sanitizeNextPath("//evil.example.com")).toBe("/app");
    expect(sanitizeNextPath("javascript:alert(1)")).toBe("/app");
    expect(sanitizeNextPath(null)).toBe("/app");
    expect(sanitizeNextPath("")).toBe("/app");
  });
});
