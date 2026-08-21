import {
  type PropsWithChildren,
  createContext,
  useContext,
  useLayoutEffect,
  useState,
} from "react";

interface PageTitleContextValue {
  title: string;
  setTitle: (title: string) => void;
}

const PageTitleContext = createContext<PageTitleContextValue | null>(null);

export function PageTitleProvider({ children }: PropsWithChildren) {
  const [title, setTitle] = useState("Spindle");
  return (
    <PageTitleContext.Provider value={{ title, setTitle }}>{children}</PageTitleContext.Provider>
  );
}

function usePageTitleContext(): PageTitleContextValue {
  const ctx = useContext(PageTitleContext);
  if (!ctx) throw new Error("usePageTitle must be used within PageTitleProvider");
  return ctx;
}

/** Route components call this to set `AppHeader`'s page title. */
export function usePageTitle(title: string): void {
  const { setTitle } = usePageTitleContext();
  useLayoutEffect(() => {
    setTitle(title);
    document.title = `${title} · Spindle`;
  }, [title, setTitle]);
}

/** `AppHeader` reads the current title. */
export function useCurrentPageTitle(): string {
  return usePageTitleContext().title;
}
