import type { Locale } from "./core";

const names = `AAPL|苹果|Apple
MSFT|微软|Microsoft
AMZN|亚马逊|Amazon
GOOGL|谷歌 A 类|Alphabet Class A
GOOG|谷歌 C 类|Alphabet Class C
META|元平台|Meta Platforms
NVDA|英伟达|NVIDIA
TSLA|特斯拉|Tesla
ORCL|甲骨文|Oracle
SPY|标普500指数基金|SPDR S&P 500 ETF
QQQ|纳斯达克100指数基金|Invesco QQQ Trust
DIA|道琼斯工业平均指数基金|SPDR Dow Jones Industrial Average ETF
IWM|罗素2000指数基金|iShares Russell 2000 ETF
VTI|美国全市场指数基金|Vanguard Total Stock Market ETF
RSP|标普500等权重指数基金|Invesco S&P 500 Equal Weight ETF
XLB|原材料行业指数基金|Materials Select Sector SPDR Fund
XLC|通信服务行业指数基金|Communication Services Select Sector SPDR Fund
XLE|能源行业指数基金|Energy Select Sector SPDR Fund
XLF|金融行业指数基金|Financial Select Sector SPDR Fund
XLI|工业行业指数基金|Industrial Select Sector SPDR Fund
XLK|科技行业指数基金|Technology Select Sector SPDR Fund
XLP|必需消费行业指数基金|Consumer Staples Select Sector SPDR Fund
XLRE|房地产行业指数基金|Real Estate Select Sector SPDR Fund
XLU|公用事业行业指数基金|Utilities Select Sector SPDR Fund
XLV|医疗保健行业指数基金|Health Care Select Sector SPDR Fund
XLY|非必需消费行业指数基金|Consumer Discretionary Select Sector SPDR Fund
SMH|半导体行业指数基金|VanEck Semiconductor ETF
IYR|美国房地产指数基金|iShares U.S. Real Estate ETF
TLT|美国20年以上国债指数基金|iShares 20+ Year Treasury Bond ETF
EWJ|日本股票指数基金|iShares MSCI Japan ETF
EWY|韩国股票指数基金|iShares MSCI South Korea ETF
EWT|台湾股票指数基金|iShares MSCI Taiwan ETF
INDA|印度股票指数基金|iShares MSCI India ETF
EWU|英国股票指数基金|iShares MSCI United Kingdom ETF
EWG|德国股票指数基金|iShares MSCI Germany ETF
EWQ|法国股票指数基金|iShares MSCI France ETF
EWL|瑞士股票指数基金|iShares MSCI Switzerland ETF
AVGO|博通|Broadcom
CRM|赛富时|Salesforce
AMD|超威半导体|Advanced Micro Devices
INTC|英特尔|Intel
QCOM|高通|Qualcomm
MU|美光科技|Micron Technology
AMAT|应用材料|Applied Materials
LRCX|泛林集团|Lam Research
KLAC|科磊|KLA
TXN|德州仪器|Texas Instruments
CSCO|思科|Cisco Systems
NFLX|奈飞|Netflix
NOW|ServiceNow 服务管理公司|ServiceNow
WMT|沃尔玛|Walmart
COST|好市多|Costco
HD|家得宝|Home Depot
MCD|麦当劳|McDonald's
NKE|耐克|Nike
SBUX|星巴克|Starbucks
LOW|劳氏|Lowe's
TGT|塔吉特|Target
DIS|迪士尼|Walt Disney
BKNG|缤客控股|Booking Holdings
CMG|奇波雷墨西哥烧烤|Chipotle Mexican Grill
PG|宝洁|Procter & Gamble
KO|可口可乐|Coca-Cola
PEP|百事公司|PepsiCo
JPM|摩根大通|JPMorgan Chase
BAC|美国银行|Bank of America
WFC|富国银行|Wells Fargo
GS|高盛|Goldman Sachs
MS|摩根士丹利|Morgan Stanley
C|花旗集团|Citigroup
SCHW|嘉信理财|Charles Schwab
BLK|贝莱德|BlackRock
AXP|美国运通|American Express
V|维萨|Visa
MA|万事达|Mastercard
COF|第一资本|Capital One
LLY|礼来|Eli Lilly
UNH|联合健康|UnitedHealth Group
JNJ|强生|Johnson & Johnson
ABBV|艾伯维|AbbVie
MRK|默沙东|Merck
TMO|赛默飞世尔|Thermo Fisher Scientific
AMGN|安进|Amgen
GILD|吉利德科学|Gilead Sciences
ISRG|直觉外科|Intuitive Surgical
PFE|辉瑞|Pfizer
BMY|百时美施贵宝|Bristol Myers Squibb
MDT|美敦力|Medtronic
ABT|雅培|Abbott Laboratories
GE|通用电气航空航天|GE Aerospace
CAT|卡特彼勒|Caterpillar
BA|波音|Boeing
RTX|雷神技术|RTX
LMT|洛克希德马丁|Lockheed Martin
DE|迪尔|Deere
UPS|联合包裹|United Parcel Service
FDX|联邦快递|FedEx
ETN|伊顿|Eaton
HON|霍尼韦尔|Honeywell
XOM|埃克森美孚|Exxon Mobil
CVX|雪佛龙|Chevron
COP|康菲石油|ConocoPhillips
SLB|斯伦贝谢|SLB
EOG|伊欧格能源|EOG Resources
NEE|新纪元能源|NextEra Energy
LIN|林德|Linde
PLD|安博|Prologis
FCX|自由港麦克莫兰|Freeport-McMoRan
TSM|台积电|Taiwan Semiconductor Manufacturing
SKHY|爱思开海力士|SK hynix
SMCI|超微电脑|Super Micro Computer
SPCX|太空探索技术公司|SpaceX
MRVL|迈威尔科技|Marvell Technology
NXPI|恩智浦|NXP Semiconductors
ON|安森美半导体|ON Semiconductor
ASML|阿斯麦|ASML
ADI|亚德诺|Analog Devices
ARM|安谋控股|Arm Holdings
RMBS|Rambus 半导体公司|Rambus
SIMO|慧荣科技|Silicon Motion Technology
AXTI|AXT 半导体材料公司|AXT
POET|POET 光电子科技|POET Technologies
SWKS|思佳讯|Skyworks Solutions
AMKR|艾马克技术|Amkor Technology
NBIS|Nebius 云计算公司|Nebius Group
APLD|Applied Digital 数据中心公司|Applied Digital
IREN|IREN 数据中心公司|IREN
NOK|诺基亚|Nokia
SNDK|闪迪|SanDisk
WDC|西部数据|Western Digital
STX|希捷科技|Seagate Technology
LITE|朗美通|Lumentum
CIEN|思艾纳|Ciena
DELL|戴尔科技|Dell Technologies
QBTS|D-Wave 量子计算公司|D-Wave Quantum
HPQ|惠普|HP
IONQ|IonQ 量子计算公司|IonQ
RGTI|Rigetti 量子计算公司|Rigetti Computing
GLW|康宁|Corning
AEVA|Aeva 传感技术公司|Aeva Technologies
OUST|Ouster 激光雷达公司|Ouster
VRT|维谛技术|Vertiv
PLTR|帕兰提尔|Palantir Technologies
CRWV|CoreWeave 云计算公司|CoreWeave
PANW|派拓网络|Palo Alto Networks
CRWD|众击|CrowdStrike
MDB|MongoDB 数据库公司|MongoDB
ADBE|Adobe 软件公司|Adobe
NET|Cloudflare 网络服务公司|Cloudflare
FTNT|飞塔信息|Fortinet
VRSN|威瑞信|VeriSign
RKLB|火箭实验室|Rocket Lab
FLY|萤火虫航空航天|Firefly Aerospace
ASTS|太空移动通信|AST SpaceMobile
UBER|优步|Uber Technologies
SHOP|Shopify 电商平台|Shopify
GRAB|格步|Grab Holdings
BRK.B|伯克希尔 B 类|Berkshire Hathaway Class B
MSTR|微策略|Strategy
COIN|币库|Coinbase
CRCL|Circle 金融科技公司|Circle
BMNR|Bitmine 数字资产公司|Bitmine Immersion Technologies
MARA|MARA 矿业控股|MARA Holdings
CORZ|Core Scientific 数据中心公司|Core Scientific
MCO|穆迪|Moody's
SOFI|索菲科技|SoFi Technologies
HOOD|罗宾汉|Robinhood Markets
FUTU|富途控股|Futu Holdings
BULL|微牛|Webull
MFG|瑞穗金融|Mizuho Financial Group
CB|安达保险|Chubb
NVO|诺和诺德|Novo Nordisk
TEM|Tempus 人工智能公司|Tempus AI
HIMS|Hims & Hers 健康公司|Hims & Hers Health
BSX|波士顿科学|Boston Scientific
CRSP|CRISPR 基因编辑公司|CRISPR Therapeutics
SYK|史赛克|Stryker
DVA|德维特|DaVita
RDDT|红迪|Reddit
BABA|阿里巴巴|Alibaba Group
DASH|DoorDash 配送平台|DoorDash
KHC|卡夫亨氏|Kraft Heinz
SPOT|声田科技|Spotify Technology
ABNB|爱彼迎|Airbnb
ROKU|罗库|Roku
RBLX|罗布乐思|Roblox
FLUT|Flutter 娱乐公司|Flutter Entertainment
WRD|文远知行|WeRide
F|福特汽车|Ford Motor
PYPL|贝宝|PayPal
T|美国电话电报|AT&T
IBM|国际商业机器|IBM
VZ|威瑞森|Verizon
KR|克罗格|Kroger
CASY|凯西便利店|Casey's General Stores
PACK|Ranpak 包装公司|Ranpak Holdings
OXY|西方石油|Occidental Petroleum
VST|Vistra 能源公司|Vistra Energy
OKLO|奥克洛|Oklo
FLNC|Fluence 储能公司|Fluence Energy
MP|MP 稀土材料公司|MP Materials
NVTS|纳微半导体|Navitas Semiconductor
BBAI|BigBear.ai 人工智能公司|BigBear.ai
SVRN|SVRN 公司|SVRN`;

export const instrumentNames: Readonly<Record<string, readonly [string, string]>> =
  Object.fromEntries(names.split("\n").map((line) => {
    const [symbol, zh, en] = line.split("|") as [string, string, string];
    return [`US.${symbol}`, [zh, en] as const];
  }));

export function instrumentName(symbol: string, sourceName: string | null | undefined, locale: Locale): string {
  const name = instrumentNames[symbol];
  if (locale === "zh-CN" && sourceName && /\p{Script=Han}/u.test(sourceName)) return sourceName;
  if (name) return name[locale === "en-US" ? 1 : 0];
  return sourceName && (locale === "zh-CN" || !/\p{Script=Han}/u.test(sourceName))
    ? sourceName : symbol.replace(/^US\./, "");
}

export function instrumentSearchTerm(value: string): string {
  const query = value.trim().toLocaleLowerCase();
  const matches = Object.entries(instrumentNames).filter(([, names]) =>
    names.some((name) => name.toLocaleLowerCase().includes(query)),
  );
  return matches.length === 1 ? matches[0]![0].replace(/^US\./, "") : value;
}
