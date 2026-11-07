import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { useState } from "react";
import { I18nProvider, useI18n } from "./context";
import { LanguageSelect } from "./LanguageSelect";
import { LOCALE_STORAGE_KEY } from "./core";

function Fixture() {
  const { locale } = useI18n();
  const [value, setValue] = useState("");
  return <><LanguageSelect /><input aria-label="state" value={value} onChange={event => setValue(event.target.value)} /><output>{locale}</output></>;
}

describe("language preference", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
  test("switches immediately, persists, and preserves mounted input state", () => {
    const storage = { getItem: vi.fn(() => null), setItem: vi.fn() };
    vi.stubGlobal("localStorage", storage);
    render(<I18nProvider><Fixture /></I18nProvider>);
    fireEvent.change(screen.getByLabelText("state"), { target: { value: "US.SPY" } });
    fireEvent.change(screen.getByLabelText("界面语言"), { target: { value: "en-US" } });
    expect(screen.getByLabelText("Interface language")).toHaveValue("en-US");
    expect(storage.setItem).toHaveBeenCalledWith(LOCALE_STORAGE_KEY, "en-US");
    expect(document.documentElement.lang).toBe("en-US");
    expect(document.title).toBe("Signal Lab | Stock Prediction Research Workbench");
    expect(screen.getByLabelText("state")).toHaveValue("US.SPY");
  });
  test("loads saved English and synchronizes deletion without echo writes", () => {
    const storage = { getItem: vi.fn(() => "en-US"), setItem: vi.fn() };
    vi.stubGlobal("localStorage", storage);
    render(<I18nProvider><Fixture /></I18nProvider>);
    expect(screen.getByLabelText("Interface language")).toHaveValue("en-US");
    fireEvent(window, new StorageEvent("storage", { key: LOCALE_STORAGE_KEY, newValue: null }));
    expect(screen.getByLabelText("界面语言")).toHaveValue("zh-CN");
    fireEvent(window, new StorageEvent("storage", { key: LOCALE_STORAGE_KEY, newValue: "en-US" }));
    expect(screen.getByLabelText("Interface language")).toHaveValue("en-US");
    fireEvent(window, new StorageEvent("storage", { key: null }));
    expect(screen.getByLabelText("界面语言")).toHaveValue("zh-CN");
    expect(storage.setItem).not.toHaveBeenCalled();
  });
  test("remains usable when browser storage throws", () => {
    vi.stubGlobal("localStorage", { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } });
    render(<I18nProvider><Fixture /></I18nProvider>);
    fireEvent.change(screen.getByLabelText("界面语言"), { target: { value: "en-US" } });
    expect(screen.getByLabelText("Interface language")).toHaveValue("en-US");
  });
});
