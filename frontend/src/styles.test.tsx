import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "./styles.css";

describe("light workspace semantic primitives", () => {
  it("keeps primary and destructive actions, disabled actions, dialog, and technical panel on explicit semantic classes", () => {
    render(
      <>
        <button className="df-button df-button-primary">开始开发</button>
        <button className="df-button df-button-danger">永久删除</button>
        <button className="df-button df-button-primary" disabled>正在提交</button>
        <section role="dialog" className="df-dialog">删除确认</section>
        <section className="df-technical-panel">技术详情</section>
      </>,
    );

    expect(screen.getByRole("button", { name: "开始开发" })).toHaveClass("df-button", "df-button-primary");
    expect(screen.getByRole("button", { name: "永久删除" })).toHaveClass("df-button", "df-button-danger");
    expect(screen.getByRole("button", { name: "正在提交" })).toBeDisabled();
    expect(screen.getByRole("dialog")).toHaveClass("df-dialog");
    expect(screen.getByText("技术详情")).toHaveClass("df-technical-panel");
  });
});
