/** 全局 Toast：监听 client.ts 派发的 aitf-toast 事件（与旧版 showToast 行为一致） */
import { useEffect, useState } from "react";

let hideTimer: ReturnType<typeof setTimeout> | undefined;

export default function ToastHost() {
  const [msg, setMsg] = useState("");
  const [show, setShow] = useState(false);

  useEffect(() => {
    const on = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail || "";
      setMsg(detail);
      setShow(true);
      clearTimeout(hideTimer);
      hideTimer = setTimeout(() => setShow(false), 2200);
    };
    window.addEventListener("aitf-toast", on);
    return () => window.removeEventListener("aitf-toast", on);
  }, []);

  if (!show) return null;
  return <div className="toast show">{msg}</div>;
}
