import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";
import { resetGeneratorRefs } from "../TcGenerator";

// findBy*/waitFor ждут 1 с по умолчанию — под нагрузкой полного прогона тяжёлые экраны (генератор) не успевают.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => { cleanup(); resetGeneratorRefs(); });
