export const curriculumUrl = 'https://mentor.sjtu.edu.cn/jwc/plans/';
export const departmentDirectoryUrl = 'https://www.sjtu.edu.cn/jgsz/';
export const customCollege = 'other-college';
export const customMajor = 'other-major';

const engineering = 'engineering';
const science = 'science';
const business = 'business';
const other = 'other';

// Undergraduate curriculum catalogue and programme directories. Some units also
// list their verified graduate programmes. The IDs are local and stay stable.
const rows = [
  ['cs', '计算机学院（网络空间安全学院、密码学院）', engineering, { cs: '计算机科学与技术', software: '软件工程', security: '信息安全', cyberspace: '网络空间安全' }, 'https://www.cs.sjtu.edu.cn/bks_notice.html'],
  ['me', '机械与动力工程学院', engineering, { mechanical: '机械工程', energy: '能源与动力工程', industrial: '工业工程', storage: '储能科学与工程', robotics: '机器人工程' }],
  ['naoce', '船舶海洋与建筑工程学院', engineering, { naval: '船舶与海洋工程', civil: '土木工程', mechanics: '工程力学', oceanTech: '海洋智能与无人技术', oceanClass: '海洋工程与土建类' }],
  ['seiee', '电子信息与电气工程学院（学部）', engineering, { infoClass: '工科试验班（信息类）', csIeee: '计算机科学与技术（IEEE试点班）', securityIeee: '信息安全（IEEE试点班）', infoIeee: '信息工程（IEEE试点班）', automationIeee: '自动化（IEEE试点班）' }],
  ['see', '电气工程学院', engineering, { electrical: '电气工程及其自动化', electricalSiyuan: '电气工程及其自动化（思源试点班）' }],
  ['sais', '自动化与感知学院', engineering, { automation: '自动化', sensing: '智能感知工程', robotics: '机器人工程' }],
  ['icisee', '集成电路学院（信息与电子工程学院）', engineering, { information: '信息工程', electronics: '电子科学与技术', microelectronics: '微电子科学与工程' }],
  ['ai', '人工智能学院', engineering, { ai: '人工智能', embodiedAi: '具身智能', intelligence: '智能科学与技术' }, 'https://soai.sjtu.edu.cn/'],
  ['mse', '材料科学与工程学院', engineering, { materials: '材料科学与工程' }],
  ['saa', '航空航天学院', engineering, { aerospace: '航空航天工程' }],
  ['sese', '环境科学与工程学院', engineering, { environment: '环境科学与工程' }],
  ['bme', '生物医学工程学院', engineering, { biomedical: '生物医学工程' }],
  ['energy', '智慧能源创新学院', engineering, { smartEnergy: '智慧能源工程' }],
  ['math', '数学科学学院', science, { math: '数学与应用数学', statistics: '统计学' }],
  ['physics', '物理与天文学院', science, { physics: '物理学', astronomy: '天文学' }],
  ['chem', '化学化工学院', science, { chemistry: '化学', chemBio: '化学生物学', chemicalEngineering: '化学工程与工艺' }],
  ['ocean', '海洋学院', science, { oceanScience: '海洋科学' }],
  ['life', '生命科学技术学院', science, { biotech: '生物技术', lifeEngineering: '生物工程', bioscience: '生物科学', biology: '生物学' }],
  ['scsb', '系统生物医学研究院', science, { biology: '生物学（系统生物医学方向）' }, 'https://scsb.sjtu.edu.cn/EnrolmentInformation.html'],
  ['zhiyuan', '致远学院', science, { math: '数学与应用数学（致远荣誉计划）', physics: '物理学（致远荣誉计划）', bioscience: '生物科学（致远荣誉计划）', cs: '计算机科学与技术（致远荣誉计划）', chemistry: '化学（致远荣誉计划）' }, 'https://zhiyuan.sjtu.edu.cn/teaching/enroll'],
  ['agri', '农业与生物学院', other, { agriculture: '智慧农业', agriResources: '农业资源与环境', food: '食品科学与工程', animal: '动物科学', plant: '植物科学与技术' }],
  ['pharm', '药学院', other, { pharmacy: '药学', clinicalPharmacy: '临床药学' }],
  ['psy', '心理学院', other, { psychology: '心理学' }],
  ['acem', '安泰经济与管理学院', business, { finance: '金融学', economics: '经济学', accounting: '会计学', management: '工商管理（市场营销）', hr: '人力资源管理', dataManagement: '大数据管理与应用' }],
  ['law', '凯原法学院', other, { law: '法学', lawClass: '法学试验班' }],
  ['sfl', '外国语学院', other, { english: '英语', japanese: '日语', german: '德语' }],
  ['humanities', '人文学院', other, { literature: '汉语言文学', language: '汉语言' }],
  ['sipa', '国际与公共事务学院', other, { administration: '行政管理', ppe: '政治学、经济学与哲学' }],
  ['media', '媒体与传播学院', other, { communication: '传播学', television: '广播电视编导', culture: '文化产业管理', journalism: '新闻传播学' }],
  ['design', '设计学院', other, { product: '工业设计', visual: '视觉传达设计', habitat: '人居设计' }],
  ['icci', '国际文化创意产业学院', other, { digitalCulture: '数字文创与管理' }, 'https://icci.sjtu.edu.cn/announcement/category/29'],
  ['shsmu', '医学院', other, { medicine: '临床医学', dental: '口腔医学' }],
  ['pujiang', '浦江国际学院', engineering, { ece: '电子与计算机工程', cs: '计算机科学与技术', dataScience: '数据科学与大数据技术', mechanical: '机械工程' }],
  ['speit', '巴黎卓越工程师学院', engineering, { mechanical: '机械工程', energy: '能源与动力工程', information: '信息工程', french: '法语' }],
];

export const colleges = Object.fromEntries(rows.map(([id, name, category, majors, noticeUrl]) => [
  id, {
    name, category, noticeUrl,
    majors: Object.fromEntries(Object.entries(majors).map(([major, label]) => [id + '-' + major, label])),
  },
]));

export const collegeNames = Object.fromEntries(Object.entries(colleges).map(([id, college]) => [id, college.name]));
export const majorNames = Object.assign({}, ...Object.values(colleges).map((college) => college.majors));

export function majorsFor(collegeId) {
  return Object.hasOwn(colleges, collegeId) ? colleges[collegeId].majors : {};
}

export function selectedMajorCategory(collegeId, majorId) {
  if (!Object.hasOwn(majorsFor(collegeId), majorId)) return null;
  if (majorId === 'chem-chemicalEngineering' || majorId === 'life-lifeEngineering') return engineering;
  if (majorId === 'speit-french') return other;
  return colleges[collegeId].category;
}
