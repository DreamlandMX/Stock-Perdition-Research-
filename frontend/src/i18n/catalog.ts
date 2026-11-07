import { commonMessages } from "./common-messages";
import { presentationMessages } from "./presentation-messages";
import { sharedMessages } from "./shared-messages";
import { navigationMessages } from "./navigation-messages";
import { backendMessages } from "./backend-messages";
import { overviewMessages } from "./overview-messages";
import { optionsMessages } from "./options-messages";
import { researchMessages } from "./research-messages";
import { screenMessages } from "./screen-messages";
import { healthMessages } from "./health-messages";
import { backtestsMessages } from "./backtests-messages";
import { operationsMessages } from "./operations-messages";
import { m7Messages } from "./m7-messages";

export const messages = {
  "common.topFive": { zh: "前 5 名", en: "Top 5" },
  "options.callWall": { zh: "看涨期权持仓墙", en: "Call Wall" },
  "options.putWall": { zh: "看跌期权持仓墙", en: "Put Wall" },
  ...commonMessages, ...presentationMessages, ...sharedMessages, ...navigationMessages, ...backendMessages, ...overviewMessages, ...optionsMessages,
  ...researchMessages, ...screenMessages, ...healthMessages,
  ...backtestsMessages, ...operationsMessages, ...m7Messages,
} as const satisfies Record<string, { readonly zh: string; readonly en: string }>;
