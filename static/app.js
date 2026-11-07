"use strict";

const STATUS_OPTIONS = [
  "Playing",
  "Plan to Play",
  "Not Played",
  "Beaten",
  "Completed",
  "Dropped",
];

const COMMON_GENRES = [
  "Action",
  "Action RPG",
  "Action-Adventure",
  "Adventure",
  "FPS",
  "Hack and Slash",
  "Horror",
  "Immersive Sim",
  "Interactive Drama",
  "Metroidvania",
  "Platformer",
  "Puzzle",
  "Puzzle Platformer",
  "Racing",
  "Roguelike",
  "RPG",
  "Sandbox",
  "Shooter",
  "Simulation",
  "Soulslike",
  "Sports",
  "Strategy",
  "Survival",
  "Tactical",
  "Tactical RPG",
  "Third-Person Shooter",
  "Turn-Based Strategy",
  "Visual Novel",
];

const COMMON_TAGS = [
  "Casual",
  "Co-op",
  "Comedy",
  "Crafting",
  "Dark",
  "Detective",
  "Exploration",
  "Fantasy",
  "Free To Play",
  "Horror",
  "Indie",
  "Multiplayer",
  "Open World",
  "Post-Apocalyptic",
  "Psychological",
  "Puzzle",
  "Real-Time",
  "Rhythm",
  "Sci-Fi",
  "Space",
  "Stealth",
  "Story-Rich",
  "Superhero",
  "Supernatural",
  "Survival Horror",
  "Tower Defense",
  "Turn-Based",
];

const state = {
  games: [],
  allGenres: [...COMMON_GENRES],
  allTags: [...COMMON_TAGS],
  platform: "",
  status: "",
  ownership: "",
  gamePassOnly: false,
  genre: "",
  tag: "",
  search: "",
  sortBy: "playtime_desc",
  favoriteOnly: false,
  viewMode: localStorage.getItem("vertex_view_mode") || "grid",
  activeTab: localStorage.getItem("vertex_active_tab") || "library",
  habitsData: null,
  triageQueue: [],
  triageIndex: 0,
  triageScope: "unlocked",
  reconcileQueue: [],
  reconcileIndex: 0,
  reconcileScope: "all",
  reconcileCheckedIds: new Set(),
  manualSelectedGames: [],
  reconcileCandidatesCount: 0,
  playtimePrompts: [],
  currentGameAchievements: null,
  currentAchFilter: "all",
  currentAchPlatform: "all",
  currentAchSearch: "",
  wishlistItems: [],
  wishlistDeals: [],
  wishlistFilterStatus: "active",
  wishlistFilterPlatform: "",
  wishlistFilterSmartOnly: false,
  wishlistSearch: "",
  selectedCandidate: null,
  pushSubscribed: false,
};

const LUCIDE_ICONS = {
  library: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m16 6 4 14"/><path d="M12 6v14"/><path d="M8 8v12"/><path d="M4 4v16"/></svg>`,
  wishlist: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>`,
  insights: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`,
  settings: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
  sync: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>`,
  reconcile: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 17H7A5 5 0 0 1 7 7h2"/><path d="M15 7h2a5 5 0 1 1 0 10h-2"/><line x1="8" y1="12" x2="16" y2="12"/></svg>`,
  triage: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m13 2-2 10h6L7 22l2-10H3z"/></svg>`,
  clock: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
  trophy: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/></svg>`,
  star: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
  starFilled: `<svg class="lucide-icon lucide-star-filled" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`,
  plus: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
  more: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>`,
  close: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  check: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
  trash: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`,
  bell: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>`,
  bellOff: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8.7 3A6 6 0 0 1 18 8a21.3 21.3 0 0 0 .6 5"/><path d="M17 17H3s3-2 3-9a4.67 4.67 0 0 1 .3-1.7"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/><line x1="2" y1="2" x2="22" y2="22"/></svg>`,
  lock: `<svg class="lucide-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>`,
};

const BRAND_ICONS = {
  steam: `<svg class="brand-icon brand-icon-steam" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path fill="currentColor" d="M11.979 0C5.678 0 .511 4.86.022 11.037l6.432 2.658c.545-.371 1.203-.59 1.912-.59.063 0 .125.004.188.006l2.861-4.142V8.91c0-2.495 2.028-4.524 4.524-4.524 2.494 0 4.524 2.031 4.524 4.527s-2.03 4.525-4.524 4.525h-.105l-4.076 2.911c0 .052.004.105.004.159 0 1.875-1.515 3.396-3.39 3.396-1.635 0-3.016-1.173-3.331-2.727L.436 15.27C1.862 20.307 6.486 24 11.979 24c6.627 0 11.999-5.373 11.999-12S18.605 0 11.979 0zM7.54 18.21l-1.473-.61c.262.543.714.999 1.314 1.25 1.297.539 2.793-.076 3.332-1.375.263-.63.264-1.319.005-1.949s-.75-1.121-1.377-1.383c-.624-.26-1.29-.249-1.878-.03l1.523.63c.956.4 1.409 1.5 1.009 2.455-.397.957-1.497 1.41-2.454 1.012H7.54zm11.415-9.303c0-1.662-1.353-3.015-3.015-3.015-1.665 0-3.015 1.353-3.015 3.015 0 1.665 1.35 3.015 3.015 3.015 1.663 0 3.015-1.35 3.015-3.015zm-5.273-.005c0-1.252 1.013-2.266 2.265-2.266 1.249 0 2.266 1.014 2.266 2.266 0 1.251-1.017 2.265-2.266 2.265-1.253 0-2.265-1.014-2.265-2.265z"/></svg>`,
  xbox: `<svg class="brand-icon brand-icon-xbox" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path fill="currentColor" d="M4.102 21.033C6.211 22.881 8.977 24 12 24c3.026 0 5.789-1.119 7.902-2.967 1.877-1.912-4.316-8.709-7.902-11.417-3.582 2.708-9.779 9.505-7.898 11.417zm11.16-14.406c2.5 2.961 7.484 10.313 6.076 12.912C23.002 17.48 24 14.861 24 12.004c0-3.34-1.365-6.362-3.57-8.536 0 0-.027-.022-.082-.042-.063-.022-.152-.045-.281-.045-.592 0-1.985.434-4.805 3.246zM3.654 3.426c-.057.02-.082.041-.086.042C1.365 5.642 0 8.664 0 12.004c0 2.854.998 5.473 2.661 7.533-1.401-2.605 3.579-9.951 6.08-12.91-2.82-2.813-4.216-3.245-4.806-3.245-.131 0-.223.021-.281.046v-.002zM12 3.551S9.055 1.828 6.755 1.746c-.903-.033-1.454.295-1.521.339C7.379.646 9.659 0 11.984 0H12c2.334 0 4.605.646 6.766 2.085-.068-.046-.615-.372-1.52-.339C14.946 1.828 12 3.545 12 3.545v.006z"/></svg>`,
  playstation: `<svg class="brand-icon brand-icon-playstation" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path fill="currentColor" d="M8.984 2.596v17.547l3.915 1.261V6.688c0-.69.304-1.151.794-.991.636.18.76.814.76 1.505v5.875c2.441 1.193 4.362-.002 4.362-3.152 0-3.237-1.126-4.675-4.438-5.827-1.307-.448-3.728-1.186-5.39-1.502zm4.656 16.241l6.296-2.275c.715-.258.826-.625.246-.818-.586-.192-1.637-.139-2.357.123l-4.205 1.5V14.98l.24-.085s1.201-.42 2.913-.615c1.696-.18 3.785.03 5.437.661 1.848.601 2.04 1.472 1.576 2.072-.465.6-1.622 1.036-1.622 1.036l-8.544 3.107V18.86zM1.807 18.6c-1.9-.545-2.214-1.668-1.352-2.32.801-.586 2.16-1.052 2.16-1.052l5.615-2.013v2.313L4.205 17c-.705.271-.825.632-.239.826.586.195 1.637.15 2.343-.12L8.247 17v2.074c-.12.03-.256.044-.39.073-1.939.331-3.996.196-6.038-.479z"/></svg>`,
  switch: `<svg class="brand-icon brand-icon-switch" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path fill="currentColor" d="M14.176 24h3.674c3.376 0 6.15-2.774 6.15-6.15V6.15C24 2.775 21.226 0 17.85 0H14.1c-.074 0-.15.074-.15.15v23.7c-.001.076.075.15.226.15zm4.574-13.199c1.351 0 2.399 1.125 2.399 2.398 0 1.352-1.125 2.4-2.399 2.4-1.35 0-2.4-1.049-2.4-2.4-.075-1.349 1.05-2.398 2.4-2.398zM11.4 0H6.15C2.775 0 0 2.775 0 6.15v11.7C0 21.226 2.775 24 6.15 24h5.25c.074 0 .15-.074.15-.149V.15c.001-.076-.075-.15-.15-.15zM9.676 22.051H6.15c-2.326 0-4.201-1.875-4.201-4.201V6.15c0-2.326 1.875-4.201 4.201-4.201H9.6l.076 20.102zM3.75 7.199c0 1.275.975 2.25 2.25 2.25s2.25-.975 2.25-2.25c0-1.273-.975-2.25-2.25-2.25s-2.25.977-2.25 2.25z"/></svg>`,
  gamepass: `<svg class="brand-icon brand-icon-gp" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" focusable="false"><path fill="currentColor" d="M4.102 21.033C6.211 22.881 8.977 24 12 24c3.026 0 5.789-1.119 7.902-2.967 1.877-1.912-4.316-8.709-7.902-11.417-3.582 2.708-9.779 9.505-7.898 11.417zm11.16-14.406c2.5 2.961 7.484 10.313 6.076 12.912C23.002 17.48 24 14.861 24 12.004c0-3.34-1.365-6.362-3.57-8.536 0 0-.027-.022-.082-.042-.063-.022-.152-.045-.281-.045-.592 0-1.985.434-4.805 3.246zM3.654 3.426c-.057.02-.082.041-.086.042C1.365 5.642 0 8.664 0 12.004c0 2.854.998 5.473 2.661 7.533-1.401-2.605 3.579-9.951 6.08-12.91-2.82-2.813-4.216-3.245-4.806-3.245-.131 0-.223.021-.281.046v-.002zM12 3.551S9.055 1.828 6.755 1.746c-.903-.033-1.454.295-1.521.339C7.379.646 9.659 0 11.984 0H12c2.334 0 4.605.646 6.766 2.085-.068-.046-.615-.372-1.52-.339C14.946 1.828 12 3.545 12 3.545v.006z"/></svg>`,
  gamepass_logo: `<svg class="brand-logo-gp" viewBox="0 0 261.629 44.604" height="12" aria-hidden="true" focusable="false"><path fill="currentColor" d="M238.473,234.386a.135.135,0,0,1,.073.036c6.669,5.073,18.09,17.622,14.608,21.167a22.3,22.3,0,0,1-29.361,0c-3.482-3.545,7.937-16.094,14.606-21.167A.14.14,0,0,1,238.473,234.386Zm11.618-14.623a22.312,22.312,0,0,0-23.235,0,.085.085,0,0,0-.037.111.088.088,0,0,0,.108.039c4.318-.933,10.878,2.8,11.5,3.171a.073.073,0,0,0,.094,0c.622-.374,7.181-4.1,11.5-3.171a.088.088,0,0,0,.108-.039A.085.085,0,0,0,250.091,219.763ZM222.863,222.9a.411.411,0,0,0-.1.075,22.3,22.3,0,0,0-2.032,29.338.1.1,0,0,0,.119.036.092.092,0,0,0,.026-.115c-1.7-5.256,6.992-18,11.481-23.342a.1.1,0,0,0,.03-.064.1.1,0,0,0-.039-.074C225.547,221.987,223.237,222.706,222.863,222.9Zm21.734,5.855a.1.1,0,0,0-.038.074.1.1,0,0,0,.03.064c4.489,5.343,13.181,18.086,11.481,23.342a.092.092,0,0,0,.026.115.1.1,0,0,0,.119-.036,22.3,22.3,0,0,0-2.032-29.338.409.409,0,0,0-.1-.075C253.709,222.706,251.4,221.987,244.6,228.754Zm69.8-3.023h-4.633L299.3,252.54h4.252l2.59-6.945h11.882l2.59,6.945h4.252Zm-6.951,16.293,4.635-12.273,4.635,12.273Zm-10.292-3.6v14.119H293.3V249.03a9.674,9.674,0,0,1-3.641,3.065,11.618,11.618,0,0,1-5.047,1.046,13.375,13.375,0,0,1-5.205-.97,11.059,11.059,0,0,1-4.022-2.91,12.738,12.738,0,0,1-2.626-4.452,18.88,18.88,0,0,1,.009-11.392,12.507,12.507,0,0,1,2.655-4.444,11.305,11.305,0,0,1,4.106-2.882,14.008,14.008,0,0,1,5.308-.96,12.823,12.823,0,0,1,8.171,2.561,10.6,10.6,0,0,1,3.844,6.9l.007.045h-4.207l-.006-.031a6.883,6.883,0,0,0-2.669-4.256,8.345,8.345,0,0,0-5.1-1.569,7.9,7.9,0,0,0-6.352,2.8,13.13,13.13,0,0,0,0,15.116,8.121,8.121,0,0,0,6.427,2.8,8.455,8.455,0,0,0,5.867-2.167,7.351,7.351,0,0,0,2.5-5.413h-7.882V238.42Zm65.565-9.007v7.768h13.317v3.42H362.719v8.256h15.054v3.683h-19.15V225.731h19.15v3.683Zm-8.191-3.683V252.54h-3.945V231.266c-.507,1.493-1.187,3.344-2.026,5.518L342.5,252.54H339.07l-.01-.025-6.119-15.73c-.728-1.942-1.4-3.767-1.989-5.442v21.2h-3.945V225.731h5.622l5.491,14.253c1.1,2.887,2.01,5.388,2.7,7.437.813-2.435,1.735-5.062,2.741-7.811l5.3-13.879Zm51.668,2.282a9.53,9.53,0,0,0-6.784-2.282H388V252.54h4.1v-9.874h7.319a9.53,9.53,0,0,0,6.784-2.282,9.129,9.129,0,0,0,0-12.371Zm-14.1,1.325h7.244a5.266,5.266,0,0,1,3.8,1.265,4.828,4.828,0,0,1,1.3,3.613,4.764,4.764,0,0,1-1.3,3.594,5.315,5.315,0,0,1-3.8,1.247h-7.244ZM453.955,245.1a7.076,7.076,0,0,1-2.774,5.888c-1.838,1.425-4.416,2.148-7.664,2.148-3.522,0-6.3-.811-8.265-2.412a8.7,8.7,0,0,1-3-7.05v-.04h4.1v.039a5.635,5.635,0,0,0,1.863,4.475,8.338,8.338,0,0,0,5.381,1.493,7.746,7.746,0,0,0,4.538-1.138,3.814,3.814,0,0,0,1.579-3.291,3.143,3.143,0,0,0-1.3-2.69,13.578,13.578,0,0,0-4.715-1.7l-2.29-.488a13.2,13.2,0,0,1-6.25-2.712,6.655,6.655,0,0,1-2.115-5.21,6.388,6.388,0,0,1,2.568-5.306,11.111,11.111,0,0,1,6.932-1.979c3.5,0,6.164.761,7.928,2.262a7.985,7.985,0,0,1,2.735,6.224v.04h-4.056l0-.036a5.082,5.082,0,0,0-1.693-3.783c-1-.8-2.642-1.212-4.874-1.212a6.945,6.945,0,0,0-3.994.969,3.169,3.169,0,0,0-1.372,2.746,3.244,3.244,0,0,0,1.223,2.747,11.45,11.45,0,0,0,4.565,1.72l2.327.488c3.046.649,5.269,1.6,6.607,2.825A6.456,6.456,0,0,1,453.955,245.1Zm23.846,0a7.076,7.076,0,0,1-2.774,5.888c-1.838,1.425-4.416,2.148-7.664,2.148-3.522,0-6.3-.811-8.265-2.412a8.7,8.7,0,0,1-3-7.05v-.04h4.1v.039a5.634,5.634,0,0,0,1.863,4.475,8.338,8.338,0,0,0,5.381,1.493,7.745,7.745,0,0,0,4.538-1.138,3.814,3.814,0,0,0,1.579-3.291,3.142,3.142,0,0,0-1.3-2.69,13.577,13.577,0,0,0-4.715-1.7l-2.29-.488a13.2,13.2,0,0,1-6.25-2.712,6.654,6.654,0,0,1-2.115-5.21,6.388,6.388,0,0,1,2.568-5.306,11.111,11.111,0,0,1,6.932-1.979c3.5,0,6.164.761,7.928,2.262a7.985,7.985,0,0,1,2.736,6.224v.04h-4.056l0-.036a5.083,5.083,0,0,0-1.694-3.783c-1-.8-2.642-1.212-4.875-1.212a6.944,6.944,0,0,0-3.994.969,3.169,3.169,0,0,0-1.372,2.746,3.244,3.244,0,0,0,1.223,2.747,11.449,11.449,0,0,0,4.565,1.72l2.327.488c3.046.649,5.269,1.6,6.607,2.825A6.456,6.456,0,0,1,477.8,245.1Zm-56.944-19.373h-4.632L405.757,252.54h4.252l2.59-6.945h11.882l2.59,6.945h4.252Zm-6.951,16.293,4.635-12.273,4.635,12.273Z" transform="translate(-216.172 -216.5)"/></svg>`,
};

function getPlatformBrands(platformStr) {
  if (!platformStr) return [];
  const s = String(platformStr).toLowerCase();
  const brands = [];
  if (s.includes("steam")) brands.push("steam");
  if (s.includes("xbox")) brands.push("xbox");
  if (s.includes("playstation") || s.includes("ps4") || s.includes("ps5")) brands.push("playstation");
  if (s.includes("switch") || s.includes("nintendo")) brands.push("switch");
  return brands;
}

function getGamePlatformBrands(game) {
  if (!game) return [];
  const brands = new Set();
  if (Array.isArray(game.linked_games) && game.linked_games.length > 0) {
    for (const lg of game.linked_games) {
      for (const b of getPlatformBrands(lg.platform || lg.source)) {
        brands.add(b);
      }
    }
  } else {
    for (const b of getPlatformBrands(game.platform || game.source)) {
      brands.add(b);
    }
  }
  return Array.from(brands);
}

function createPlatformBrandIconsSpan(gameOrPlatform, extraClass = "") {
  const brands = typeof gameOrPlatform === "object" && gameOrPlatform !== null
    ? getGamePlatformBrands(gameOrPlatform)
    : getPlatformBrands(gameOrPlatform);
  if (brands.length === 0) return null;
  const span = document.createElement("span");
  span.className = `platform-brand-icons ${extraClass}`.trim();
  for (const b of brands) {
    if (BRAND_ICONS[b]) {
      const iconWrap = document.createElement("span");
      iconWrap.className = `badge-brand-icon badge-brand-${b}`;
      iconWrap.innerHTML = BRAND_ICONS[b];
      span.appendChild(iconWrap);
    }
  }
  return span;
}

function makeFallbackAchIcon(name) {
  const char = (name || "A").trim().charAt(0).toUpperCase() || "A";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 44 44">
    <rect width="44" height="44" rx="6" fill="#1e293b"/>
    <text x="22" y="27" fill="#94a3b8" font-family="sans-serif" font-size="16" font-weight="bold" text-anchor="middle">${char}</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function updateLibraryActionsDot() {
  const dot = document.getElementById("library-actions-badge");
  if (!dot) return;
  const untriagedCount = state.games.filter((g) => !g.status_locked).length;
  const hasPending = untriagedCount > 0 || (state.reconcileCandidatesCount || 0) > 0;
  dot.hidden = !hasPending;
}

function updateTriageButtonBadge() {
  const untriagedCount = state.games.filter((g) => !g.status_locked).length;
  const btn = document.getElementById("btn-open-triage");
  if (btn) {
    btn.innerHTML = `${LUCIDE_ICONS.triage} <span>Quick Triage</span> `;
    if (untriagedCount > 0) {
      const badge = document.createElement("span");
      badge.className = "triage-badge-pill";
      badge.textContent = untriagedCount;
      btn.appendChild(badge);
    }
  }
  const mBadge = document.getElementById("mnav-triage-badge");
  if (mBadge) {
    mBadge.textContent = untriagedCount;
    mBadge.hidden = untriagedCount <= 0;
  }
  const sheetBadge = document.getElementById("sheet-triage-badge");
  if (sheetBadge) {
    sheetBadge.textContent = untriagedCount;
    sheetBadge.hidden = untriagedCount <= 0;
  }
  updateLibraryActionsDot();
}

function updateReconcileButtonBadge(count) {
  if (typeof count === "number") {
    state.reconcileCandidatesCount = count;
  }
  const btn = document.getElementById("btn-open-reconcile");
  if (btn) {
    btn.innerHTML = `${LUCIDE_ICONS.reconcile} <span>Reconcile</span> `;
    if (state.reconcileCandidatesCount > 0) {
      const badge = document.createElement("span");
      badge.className = "triage-badge-pill";
      badge.textContent = state.reconcileCandidatesCount;
      btn.appendChild(badge);
    }
  }
  const mBadge = document.getElementById("mnav-reconcile-badge");
  if (mBadge) {
    mBadge.textContent = state.reconcileCandidatesCount;
    mBadge.hidden = state.reconcileCandidatesCount <= 0;
  }
  const sheetBadge = document.getElementById("sheet-reconcile-badge");
  if (sheetBadge) {
    sheetBadge.textContent = state.reconcileCandidatesCount;
    sheetBadge.hidden = state.reconcileCandidatesCount <= 0;
  }
  updateLibraryActionsDot();
}

function updateActiveFiltersUI() {
  const count =
    (state.genre ? 1 : 0) +
    (state.tag ? 1 : 0) +
    (state.status ? 1 : 0) +
    (state.ownership ? 1 : 0) +
    (state.gamePassOnly ? 1 : 0) +
    (state.favoriteOnly ? 1 : 0) +
    (state.sortBy !== "playtime_desc" ? 1 : 0);

  const badge = document.getElementById("active-filter-badge");
  if (badge) {
    badge.textContent = count;
    badge.hidden = count === 0;
  }

  const resetBtn = document.getElementById("btn-reset-filters");
  if (resetBtn) {
    resetBtn.hidden =
      !state.genre &&
      !state.tag &&
      !state.status &&
      !state.ownership &&
      !state.gamePassOnly &&
      !state.favoriteOnly &&
      !state.platform &&
      !state.search &&
      state.sortBy === "playtime_desc";
  }

  for (const card of document.querySelectorAll(".metric-card.metric-interactive")) {
    const targetStatus = card.dataset.filterStatus || "";
    card.classList.toggle("active", state.status === targetStatus);
  }
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function syncFilterInputs() {
  for (const p of document.querySelectorAll("#platform-pills .pill")) {
    p.classList.toggle("active", (p.dataset.platform || "") === state.platform);
  }
  const stSelect = document.getElementById("status-filter");
  if (stSelect) stSelect.value = state.status;
  const ownSelect = document.getElementById("ownership-filter");
  if (ownSelect) ownSelect.value = state.ownership;
  const gnSelect = document.getElementById("genre-filter");
  if (gnSelect) gnSelect.value = state.genre;
  const tgSelect = document.getElementById("tag-filter");
  if (tgSelect) tgSelect.value = state.tag;
  const qInput = document.getElementById("search-input");
  if (qInput) qInput.value = state.search;
  const favBtn = document.getElementById("btn-fav-toggle");
  if (favBtn) favBtn.setAttribute("aria-pressed", String(state.favoriteOnly));
  const gpBtn = document.getElementById("btn-gamepass-toggle");
  if (gpBtn) gpBtn.setAttribute("aria-pressed", String(state.gamePassOnly));
}

function refreshActiveView() {
  updateActiveFiltersUI();
  if (state.activeTab === "habits") {
    fetchHabitsAnalytics();
  } else if (state.activeTab === "wishlist") {
    fetchWishlistAndDeals();
  } else {
    fetchGames();
  }
}

function switchMainTab(tabName, shouldScroll = false) {
  state.activeTab = (tabName === "habits" || tabName === "wishlist") ? tabName : "library";
  localStorage.setItem("vertex_active_tab", state.activeTab);

  const tabLib = document.getElementById("tab-library");
  const tabHab = document.getElementById("tab-habits");
  const tabWish = document.getElementById("tab-wishlist");
  const mnavLib = document.getElementById("mnav-library");
  const mnavHab = document.getElementById("mnav-habits");
  const mnavWish = document.getElementById("mnav-wishlist");
  const libSec = document.getElementById("library-view-section");
  const habSec = document.getElementById("habits-view-section");
  const wishSec = document.getElementById("wishlist-view-section");
  const metricsBar = document.getElementById("metrics-bar");
  const controlsBar = document.getElementById("controls-bar");
  const promptsBanner = document.getElementById("playtime-prompts-banner");

  const isHabits = state.activeTab === "habits";
  const isWishlist = state.activeTab === "wishlist";
  const isLibrary = state.activeTab === "library";

  if (tabLib) {
    tabLib.classList.toggle("active", isLibrary);
    tabLib.setAttribute("aria-selected", String(isLibrary));
  }
  if (tabHab) {
    tabHab.classList.toggle("active", isHabits);
    tabHab.setAttribute("aria-selected", String(isHabits));
  }
  if (tabWish) {
    tabWish.classList.toggle("active", isWishlist);
    tabWish.setAttribute("aria-selected", String(isWishlist));
  }

  if (mnavLib) mnavLib.classList.toggle("active", isLibrary);
  if (mnavHab) mnavHab.classList.toggle("active", isHabits);
  if (mnavWish) mnavWish.classList.toggle("active", isWishlist);

  if (libSec) libSec.hidden = !isLibrary;
  if (metricsBar) metricsBar.hidden = !isLibrary;
  if (controlsBar) controlsBar.hidden = isWishlist;
  if (promptsBanner) promptsBanner.hidden = !isLibrary || (state.playtimePrompts || []).length === 0;
  if (habSec) habSec.hidden = !isHabits;
  if (wishSec) wishSec.hidden = !isWishlist;

  if (isHabits) {
    fetchHabitsAnalytics();
  } else if (isWishlist) {
    fetchWishlistAndDeals();
  } else {
    fetchGames();
  }

  if (shouldScroll) {
    document.getElementById("controls-bar")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function fetchHabitsAnalytics() {
  const params = new URLSearchParams();
  if (state.search) params.set("search", state.search);
  if (state.platform) params.set("platform", state.platform);
  if (state.status) params.set("status", state.status);
  if (state.ownership) params.set("ownership", state.ownership);
  if (state.gamePassOnly) params.set("is_game_pass", "true");
  if (state.genre) params.set("genre", state.genre);
  if (state.tag) params.set("tag", state.tag);
  if (state.favoriteOnly) params.set("favorite_only", "true");

  try {
    const res = await fetch(`/api/analytics?${params.toString()}`);
    if (!res.ok) return;
    const data = await res.json();
    state.habitsData = data;
    renderHabitsDashboard(data);
  } catch (err) {
    console.error("Failed to load habits analytics:", err);
  }
}

const STATUS_COLORS = {
  Completed: "#10b981",
  Beaten: "#34d399",
  Playing: "#3b82f6",
  "Plan to Play": "#f59e0b",
  "Not Played": "#64748b",
  Dropped: "#ef4444",
};

async function openGameById(gameId) {
  let game = state.games.find((g) => g.id === gameId);
  if (!game) {
    try {
      const res = await fetch(`/api/games?search=${encodeURIComponent(gameId)}`);
      if (res.ok) {
        const d = await res.json();
        game = (d.games || []).find((g) => g.id === gameId);
      }
    } catch (e) {
      console.error("Failed to fetch game by ID:", e);
    }
  }
  if (game) {
    openEditModal(game);
  }
}

function renderHabitsDashboard(data) {
  if (!data || !data.summary) return;
  const s = data.summary;

  // 1. KPI Pulse Strip
  const elHours = document.getElementById("hkpi-hours");
  if (elHours) elHours.textContent = `${s.total_hours.toLocaleString()} hrs`;
  const elDays = document.getElementById("hkpi-days");
  if (elDays) elDays.textContent = `~${s.gaming_days} days of gameplay`;

  const elSystem = document.getElementById("hkpi-system");
  if (elSystem) elSystem.textContent = s.dominant_system || "—";
  const elSystemDetail = document.getElementById("hkpi-system-detail");
  if (elSystemDetail) {
    const topPlat = data.platforms[0];
    elSystemDetail.textContent = topPlat
      ? `${topPlat.hours.toLocaleString()} hrs (${topPlat.playtime_pct}%)`
      : "Most played platform";
  }

  const elGenre = document.getElementById("hkpi-genre");
  if (elGenre) elGenre.textContent = s.dominant_genre || "—";
  const elGenreDetail = document.getElementById("hkpi-genre-detail");
  if (elGenreDetail) {
    const topGen = data.genres[0];
    elGenreDetail.textContent = topGen
      ? `${topGen.hours.toLocaleString()} hrs (${topGen.count} titles)`
      : "Favorite game category";
  }

  const elCompletion = document.getElementById("hkpi-completion");
  if (elCompletion) elCompletion.textContent = `${s.completion_rate}%`;
  const elCompletionDetail = document.getElementById("hkpi-completion-detail");
  if (elCompletionDetail) {
    elCompletionDetail.textContent = `${s.beaten_count} finished · ${s.played_games} started`;
  }

  const elBailout = document.getElementById("hkpi-bailout");
  if (elBailout) elBailout.textContent = `${s.avg_dropped_hours}h vs ${s.avg_beaten_hours}h`;
  const elBailoutDetail = document.getElementById("hkpi-bailout-detail");
  if (elBailoutDetail) {
    elBailoutDetail.textContent = `Drop avg (${s.avg_dropped_hours}h) vs finish avg (${s.avg_beaten_hours}h)`;
  }

  const elShame = document.getElementById("hkpi-shame");
  if (elShame) elShame.textContent = `${s.shelf_of_shame_count} games`;
  const elShameDetail = document.getElementById("hkpi-shame-detail");
  if (elShameDetail) {
    const pct = Math.round((s.shelf_of_shame_count / Math.max(s.total_games, 1)) * 100);
    elShameDetail.textContent = `${pct}% of filtered backlog`;
  }

  // 2. Active Filter Summary
  const subTitle = document.getElementById("habits-subtitle");
  if (subTitle) {
    subTitle.textContent = `Analyzing ${s.total_games} games · ${s.total_hours.toLocaleString()} total playtime hours`;
  }
  const summaryChips = document.getElementById("habits-active-filters-summary");
  if (summaryChips) {
    summaryChips.innerHTML = "";
    const chips = [];
    if (state.platform) chips.push({ label: `Platform: ${state.platform}`, key: "platform" });
    if (state.genre) chips.push({ label: `Genre: ${state.genre}`, key: "genre" });
    if (state.tag) chips.push({ label: `Tag: ${state.tag}`, key: "tag" });
    if (state.status) chips.push({ label: `Status: ${state.status}`, key: "status" });
    if (state.ownership) chips.push({ label: `Ownership: ${state.ownership}`, key: "ownership" });
    if (state.favoriteOnly) chips.push({ label: "★ Favorites", key: "favoriteOnly" });
    if (state.search) chips.push({ label: `Search: "${state.search}"`, key: "search" });

    for (const chip of chips) {
      const span = document.createElement("span");
      span.className = "habits-filter-chip";
      span.innerHTML = `<span>${escapeHtml(chip.label)}</span><button type="button" class="habits-filter-chip-clear" aria-label="Remove filter">✕</button>`;
      span.querySelector("button").addEventListener("click", () => {
        if (chip.key === "platform") state.platform = "";
        else if (chip.key === "genre") state.genre = "";
        else if (chip.key === "tag") state.tag = "";
        else if (chip.key === "status") state.status = "";
        else if (chip.key === "ownership") state.ownership = "";
        else if (chip.key === "favoriteOnly") state.favoriteOnly = false;
        else if (chip.key === "search") state.search = "";
        syncFilterInputs();
        refreshActiveView();
      });
      summaryChips.appendChild(span);
    }
  }

  // 3. Systems Comparison List
  const systemsContainer = document.getElementById("systems-comparison-list");
  if (systemsContainer) {
    systemsContainer.innerHTML = "";
    if (data.platforms.length === 0) {
      systemsContainer.innerHTML = '<p class="empty-state">No platform data found for current filters.</p>';
    } else {
      for (const plat of data.platforms) {
        let pillClass = "system-pill-other";
        let brandKey = "";
        const pLower = plat.name.toLowerCase();
        if (pLower.includes("steam")) { pillClass = "system-pill-steam"; brandKey = "steam"; }
        else if (pLower.includes("xbox")) { pillClass = "system-pill-xbox"; brandKey = "xbox"; }
        else if (pLower.includes("playstation")) { pillClass = "system-pill-playstation"; brandKey = "playstation"; }
        else if (pLower.includes("switch")) { pillClass = "system-pill-switch"; brandKey = "switch"; }
        const brandSvg = BRAND_ICONS[brandKey] || "";

        const card = document.createElement("div");
        card.className = `system-row-card ${state.platform === plat.name ? "active-filter" : ""}`;
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("title", `Click to filter by ${plat.name}`);

        const topGameSnippet = plat.top_game
          ? `
            <div class="system-top-game-snippet">
              ${plat.top_game.cover_url ? `<img src="${escapeHtml(plat.top_game.cover_url)}" alt="" class="system-top-game-img" loading="lazy">` : ""}
              <span>#1 Most Played: <strong>${escapeHtml(plat.top_game.title)}</strong> (${plat.top_game.hours}h)</span>
            </div>
          `
          : "";

        card.innerHTML = `
          <div class="system-row-header">
            <div class="system-title-group">
              <span class="system-pill-badge ${pillClass}">${brandSvg}<span>${escapeHtml(plat.name)}</span></span>
              <span class="system-hours-text">${plat.hours.toLocaleString()} hrs</span>
            </div>
            <span class="badge-subtle">${plat.completion_rate}% Finished</span>
          </div>
          <div class="dual-meters-wrap">
            <div class="meter-labels-row">
              <span>Playtime: <strong>${plat.playtime_pct}%</strong> (${plat.hours.toLocaleString()}h)</span>
              <span>Library: <strong>${plat.library_pct}%</strong> (${plat.count} games)</span>
            </div>
            <div class="meter-track" title="Playtime Share: ${plat.playtime_pct}%">
              <div class="meter-fill meter-fill-${plat.name.toLowerCase().replace(/[^a-z]/g, "")}" style="inline-size: ${Math.min(100, Math.max(2, plat.playtime_pct))}%"></div>
            </div>
            <div class="meter-track" style="block-size: 6px;" title="Ownership Share: ${plat.library_pct}%">
              <div class="meter-fill meter-fill-ownership" style="inline-size: ${Math.min(100, Math.max(2, plat.library_pct))}%"></div>
            </div>
          </div>
          ${topGameSnippet}
        `;

        const togglePlatform = () => {
          state.platform = state.platform === plat.name ? "" : plat.name;
          syncFilterInputs();
          refreshActiveView();
        };
        card.addEventListener("click", togglePlatform);
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            togglePlatform();
          }
        });

        systemsContainer.appendChild(card);
      }
    }
  }

  // 4. Status Donut Chart & Legend
  const donutEl = document.getElementById("status-donut-chart");
  const legendEl = document.getElementById("status-legend-list");
  if (donutEl && legendEl) {
    legendEl.innerHTML = "";
    const statuses = data.statuses;
    const statusOrder = ["Completed", "Beaten", "Playing", "Plan to Play", "Not Played", "Dropped"];
    let currentDeg = 0;
    const segments = [];

    for (const key of statusOrder) {
      const st = statuses[key] || { name: key, count: 0, count_pct: 0, hours: 0, hours_pct: 0 };
      const color = STATUS_COLORS[key] || "#94a3b8";
      const deg = Math.round((st.count_pct / 100) * 360);
      if (deg > 0) {
        segments.push(`${color} ${currentDeg}deg ${currentDeg + deg}deg`);
        currentDeg += deg;
      }

      const item = document.createElement("div");
      item.className = "status-legend-item";
      item.setAttribute("role", "button");
      item.setAttribute("tabindex", "0");
      item.setAttribute("title", `Click to filter by ${key}`);
      item.innerHTML = `
        <div class="status-legend-left">
          <span class="status-color-dot" style="background-color: ${color}"></span>
          <span class="status-legend-name">${escapeHtml(key)}</span>
        </div>
        <div class="status-legend-right">
          <span class="status-legend-count">${st.count}</span>
          <span>(${st.count_pct}%) &bull; ${st.hours}h</span>
        </div>
      `;

      const toggleStatus = () => {
        state.status = state.status === key ? "" : key;
        syncFilterInputs();
        refreshActiveView();
      };
      item.addEventListener("click", toggleStatus);
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggleStatus();
        }
      });

      legendEl.appendChild(item);
    }

    if (segments.length > 0 && currentDeg < 360) {
      segments.push(`${STATUS_COLORS["Not Played"]} ${currentDeg}deg 360deg`);
    }
    donutEl.style.background = segments.length > 0
      ? `conic-gradient(${segments.join(", ")})`
      : "rgba(255, 255, 255, 0.1)";

    const centerRate = document.getElementById("donut-center-rate");
    if (centerRate) centerRate.textContent = `${s.completion_rate}%`;
  }

  // 5. Genre Taste Profile Bars
  const genresContainer = document.getElementById("genres-bars-container");
  if (genresContainer) {
    genresContainer.innerHTML = "";
    if (data.genres.length === 0) {
      genresContainer.innerHTML = '<p class="empty-state">No genre data found for current selection.</p>';
    } else {
      const maxHours = Math.max(...data.genres.map((g) => g.hours), 1);
      for (const g of data.genres) {
        const bar = document.createElement("div");
        bar.className = "genre-bar-item";
        bar.setAttribute("role", "button");
        bar.setAttribute("tabindex", "0");
        bar.setAttribute("title", `Click to filter by ${g.genre}`);
        const fillPct = Math.min(100, Math.max(3, (g.hours / maxHours) * 100));

        bar.innerHTML = `
          <div class="genre-bar-header">
            <span class="genre-bar-title">${escapeHtml(g.genre)}</span>
            <span class="genre-bar-metrics">${g.hours.toLocaleString()} hrs (${g.playtime_pct}%) &bull; ${g.count} titles</span>
          </div>
          <div class="genre-meter-track" title="${g.hours} hrs">
            <div class="genre-meter-fill" style="inline-size: ${fillPct}%"></div>
          </div>
          <div class="genre-mini-badges">
            <span>✓ ${g.beaten_count} beaten</span>
            <span>▶ ${g.playing_count} playing</span>
            <span>⏳ ${g.backlog_count} backlog</span>
            <span>✕ ${g.dropped_count} dropped</span>
            <span style="margin-inline-start: auto; font-weight: 700; color: #34d399;">${g.completion_rate}% finished</span>
          </div>
        `;

        const toggleGenre = () => {
          state.genre = state.genre === g.genre ? "" : g.genre;
          syncFilterInputs();
          refreshActiveView();
        };
        bar.addEventListener("click", toggleGenre);
        bar.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggleGenre();
          }
        });

        genresContainer.appendChild(bar);
      }
    }
  }

  // 6. Commitment Tiers
  const commitmentsContainer = document.getElementById("commitments-list");
  if (commitmentsContainer) {
    commitmentsContainer.innerHTML = "";
    for (const t of data.commitments) {
      const item = document.createElement("div");
      item.className = "tier-item-card";
      item.innerHTML = `
        <div class="tier-item-header">
          <span class="tier-item-label">${escapeHtml(t.label)}</span>
          <span class="tier-item-stats">${t.count} games (${t.count_pct}%) &bull; ${t.hours.toLocaleString()}h (${t.hours_pct}%)</span>
        </div>
        <div class="meter-track" title="${t.hours} hrs">
          <div class="meter-fill" style="inline-size: ${Math.min(100, Math.max(2, t.hours_pct))}%; background: linear-gradient(90deg, #3b82f6, #06b6d4);"></div>
        </div>
      `;
      commitmentsContainer.appendChild(item);
    }
  }

  // 7. Gaming Eras & Decades
  const erasContainer = document.getElementById("eras-list");
  if (erasContainer) {
    erasContainer.innerHTML = "";
    for (const e of data.eras) {
      const item = document.createElement("div");
      item.className = "tier-item-card";
      item.innerHTML = `
        <div class="tier-item-header">
          <span class="tier-item-label">${escapeHtml(e.label)}</span>
          <span class="tier-item-stats">${e.count} games (${e.count_pct}%) &bull; ${e.hours.toLocaleString()}h (${e.hours_pct}%)</span>
        </div>
        <div class="meter-track" title="${e.hours} hrs">
          <div class="meter-fill" style="inline-size: ${Math.min(100, Math.max(2, e.hours_pct))}%; background: linear-gradient(90deg, #f59e0b, #ef4444);"></div>
        </div>
      `;
      erasContainer.appendChild(item);
    }
  }

  // 8. Hall of Fame (Top Played)
  const hofContainer = document.getElementById("hof-games-row");
  if (hofContainer) {
    hofContainer.innerHTML = "";
    if (data.hall_of_fame.length === 0) {
      hofContainer.innerHTML = '<p class="empty-state">No games found.</p>';
    } else {
      data.hall_of_fame.forEach((g, idx) => {
        const card = document.createElement("div");
        card.className = "hof-game-card";
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("title", `View ${g.title}`);

        const fallback = makeFallbackCover(g.title, g.platform);
        const coverSrc = g.cover_url || fallback;

        card.innerHTML = `
          <div class="hof-poster-wrap">
            <span class="hof-rank-badge">#${idx + 1}</span>
            <img src="${escapeHtml(coverSrc)}" alt="${escapeHtml(g.title)}" class="hof-poster-img" loading="lazy" onerror="this.src='${fallback}'">
          </div>
          <div class="hof-card-body">
            <div class="hof-card-title">${escapeHtml(g.title)}</div>
            <div class="hof-card-meta">
              <span class="system-pill-badge system-pill-other" style="font-size: 0.68rem; padding: 0.1rem 0.4rem;">${createPlatformBrandIconsSpan(g)?.outerHTML || ""}<span>${escapeHtml(g.platform)}</span></span>
              <span class="hof-hours-tag">${g.playtime_hours} hrs</span>
            </div>
          </div>
        `;
        card.addEventListener("click", () => openGameById(g.id));
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openGameById(g.id);
          }
        });
        hofContainer.appendChild(card);
      });
    }
  }

  // 9. Backlog Gems (Top Critic Rated Unplayed)
  const gemsContainer = document.getElementById("gems-games-row");
  if (gemsContainer) {
    gemsContainer.innerHTML = "";
    const gems = data.ratings.backlog_gems || [];
    if (gems.length === 0) {
      gemsContainer.innerHTML = '<p class="empty-state">No unplayed critic gems found in this filter.</p>';
    } else {
      gems.forEach((g) => {
        const card = document.createElement("div");
        card.className = "hof-game-card";
        card.setAttribute("role", "button");
        card.setAttribute("tabindex", "0");
        card.setAttribute("title", `View ${g.title}`);

        const fallback = makeFallbackCover(g.title, g.platform);
        const coverSrc = g.cover_url || fallback;

        card.innerHTML = `
          <div class="hof-poster-wrap">
            <span class="hof-critic-badge">⭐ ${g.critic_score}</span>
            <img src="${escapeHtml(coverSrc)}" alt="${escapeHtml(g.title)}" class="hof-poster-img" loading="lazy" onerror="this.src='${fallback}'">
          </div>
          <div class="hof-card-body">
            <div class="hof-card-title">${escapeHtml(g.title)}</div>
            <div class="hof-card-meta">
              <span class="system-pill-badge system-pill-other" style="font-size: 0.68rem; padding: 0.1rem 0.4rem;">${createPlatformBrandIconsSpan(g)?.outerHTML || ""}<span>${escapeHtml(g.platform)}</span></span>
              <span style="color: var(--accent-amber); font-weight: 700;">${escapeHtml(g.completion_status)}</span>
            </div>
          </div>
        `;
        card.addEventListener("click", () => openGameById(g.id));
        card.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openGameById(g.id);
          }
        });
        gemsContainer.appendChild(card);
      });
    }
  }
}

function applyViewMode(mode) {
  state.viewMode = mode === "list" ? "list" : "grid";
  localStorage.setItem("vertex_view_mode", state.viewMode);
  const grid = document.getElementById("games-grid");
  if (grid) {
    grid.classList.toggle("list-view", state.viewMode === "list");
  }
  const gridBtn = document.getElementById("btn-view-grid");
  const listBtn = document.getElementById("btn-view-list");
  if (gridBtn && listBtn) {
    const isList = state.viewMode === "list";
    gridBtn.classList.toggle("active", !isList);
    gridBtn.setAttribute("aria-pressed", String(!isList));
    listBtn.classList.toggle("active", isList);
    listBtn.setAttribute("aria-pressed", String(isList));
  }
}

function makeFallbackCover(title, platform) {
  const safeTitle = (title || "Game").replace(/[<>&"]/g, "");
  const safePlat = (platform || "").replace(/[<>&"]/g, "");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">
    <rect width="300" height="450" fill="#14161E"/>
    <rect x="16" y="16" width="268" height="418" rx="8" fill="none" stroke="rgba(255, 255, 255, 0.08)" stroke-width="1.5"/>
    <text x="150" y="200" fill="#a1a1aa" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-size="13" font-weight="500" letter-spacing="0.05em" text-anchor="middle">${safePlat.toUpperCase()}</text>
    <text x="150" y="235" fill="#f4f4f5" font-family="-apple-system, BlinkMacSystemFont, sans-serif" font-weight="600" font-size="16" text-anchor="middle">${safeTitle.slice(0, 24)}</text>
  </svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

function attachCoverAspectObserver(wrapper, img, fallbackSrc, targetSrc) {
  if (!wrapper || !img) return;

  let attemptedFallbackHeader = false;

  const handleRatio = () => {
    if (img.naturalWidth && img.naturalHeight) {
      const ratio = img.naturalWidth / img.naturalHeight;
      // 2:3 aspect ratio is ~0.667. If > 0.72 (e.g. 1.0 square or wide landscape),
      // standard object-fit: cover would crop the left and right sides.
      // Switch to ambient blurred contain mode so the entire logo and artwork remain visible.
      if (ratio > 0.72) {
        wrapper.classList.add("cover-ambient");
        const safeSrc = (img.currentSrc || img.src || "").replace(/"/g, '\\"');
        wrapper.style.setProperty("--cover-bg", `url("${safeSrc}")`);
      } else {
        wrapper.classList.remove("cover-ambient");
        wrapper.style.removeProperty("--cover-bg");
      }
    }
  };

  img.addEventListener("load", handleRatio);

  img.addEventListener("error", () => {
    // If a Steam library_600x900.jpg 404s, attempt Steam header.jpg before falling back to SVG
    if (!attemptedFallbackHeader && img.src && img.src.includes("steamstatic.com") && img.src.includes("library_600x900.jpg")) {
      attemptedFallbackHeader = true;
      img.src = img.src.replace("library_600x900.jpg", "header.jpg");
      return;
    }
    wrapper.classList.remove("cover-ambient");
    wrapper.style.removeProperty("--cover-bg");
    img.src = fallbackSrc;
  });

  if (targetSrc) {
    img.src = targetSrc;
  }

  if (img.complete && img.naturalWidth) {
    handleRatio();
  }
}

async function resolvePlaytimePrompt(promptId, action) {
  try {
    const res = await fetch(`/api/playtime-prompts/${promptId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    if (!res.ok) return;
    await fetchStats();
    refreshActiveView();
  } catch (err) {
    console.error("Failed to resolve playtime prompt:", err);
  }
}

function renderPlaytimePrompts(prompts) {
  state.playtimePrompts = Array.isArray(prompts) ? prompts : [];
  const bannerEl = document.getElementById("playtime-prompts-banner");
  if (!bannerEl) return;

  if (state.playtimePrompts.length === 0 || state.activeTab === "habits") {
    bannerEl.hidden = true;
    bannerEl.innerHTML = "";
    return;
  }

  bannerEl.hidden = false;
  bannerEl.innerHTML = "";

  for (const p of state.playtimePrompts) {
    const card = document.createElement("article");
    card.className = "playtime-prompt-card";

    const fallbackSrc = makeFallbackCover(p.title, p.platform);
    const coverSrc = p.cover_url || fallbackSrc;

    const left = document.createElement("div");
    left.className = "playtime-prompt-left";

    const thumb = document.createElement("img");
    thumb.className = "playtime-prompt-thumb";
    thumb.src = coverSrc;
    thumb.alt = "";
    thumb.width = 44;
    thumb.height = 66;
    thumb.loading = "lazy";
    thumb.onerror = () => {
      thumb.src = fallbackSrc;
    };
    left.appendChild(thumb);

    const info = document.createElement("div");
    info.className = "playtime-prompt-info";

    const isReplay = p.current_status === "Beaten" || p.current_status === "Completed";
    const kickerText = isReplay ? "Replay Activity Detected" : "Recent Playtime Detected";

    info.innerHTML = `
      <div class="playtime-prompt-kicker">
        <span>${escapeHtml(kickerText)}</span>
        <span class="playtime-prompt-delta">+${p.delta_hours} hrs</span>
        <span class="reconcile-copy-plat">${escapeHtml(p.platform)}</span>
      </div>
      <div class="playtime-prompt-text">
        Mark <strong>${escapeHtml(p.title)}</strong> as <strong>Playing</strong>?
      </div>
      <div class="playtime-prompt-sub">
        Playtime increased from ${p.previous_hours}h to ${p.new_hours}h &bull; Current status: <strong>${escapeHtml(p.current_status)}</strong>
      </div>
    `;
    left.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "playtime-prompt-actions";

    const acceptBtn = document.createElement("button");
    acceptBtn.type = "button";
    acceptBtn.className = "btn btn-primary btn-xs";
    acceptBtn.textContent = "▶ Mark as Playing";
    acceptBtn.addEventListener("click", () => resolvePlaytimePrompt(p.prompt_id, "accept"));

    const dismissBtn = document.createElement("button");
    dismissBtn.type = "button";
    dismissBtn.className = "btn btn-secondary btn-xs";
    dismissBtn.textContent = `Dismiss for now (Keep ${p.current_status})`;
    dismissBtn.addEventListener("click", () => resolvePlaytimePrompt(p.prompt_id, "dismiss"));

    const muteBtn = document.createElement("button");
    muteBtn.type = "button";
    muteBtn.className = "btn-link-subtle";
    muteBtn.textContent = "Don't ask again";
    muteBtn.title = "Never prompt on playtime increases for this game";
    muteBtn.addEventListener("click", () => resolvePlaytimePrompt(p.prompt_id, "mute"));

    actions.appendChild(acceptBtn);
    actions.appendChild(dismissBtn);
    actions.appendChild(muteBtn);

    card.appendChild(left);
    card.appendChild(actions);
    bannerEl.appendChild(card);
  }
}

async function fetchStats() {
  try {
    const res = await fetch("/api/stats");
    if (res.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
      return;
    }
    if (!res.ok) return;
    const stats = await res.json();

    const logoutBtn = document.getElementById("btn-logout");
    if (logoutBtn) {
      logoutBtn.hidden = !stats.auth_enabled;
    }
    const mobileLogoutBtn = document.getElementById("btn-mobile-logout");
    if (mobileLogoutBtn) {
      mobileLogoutBtn.hidden = !stats.auth_enabled;
    }
    const settingsLogoutBtn = document.getElementById("btn-settings-logout");
    if (settingsLogoutBtn) {
      settingsLogoutBtn.hidden = !stats.auth_enabled;
    }

    const adminTab = document.getElementById("stab-admin-users");
    if (adminTab && typeof stats.is_admin === "boolean") {
      adminTab.hidden = !stats.is_admin;
    }


    const totalGames = (stats.total_games || 0).toLocaleString();
    const totalHours = (stats.total_playtime_hours || 0).toLocaleString();
    const totalGamesEl = document.getElementById("stat-total-games");
    if (totalGamesEl) totalGamesEl.textContent = totalGames;
    const totalHoursEl = document.getElementById("stat-total-hours");
    if (totalHoursEl) totalHoursEl.textContent = totalHours;

    const statPlaying = document.getElementById("stat-playing");
    if (statPlaying) statPlaying.textContent = stats.playing_count || 0;
    const statBacklog = document.getElementById("stat-backlog");
    if (statBacklog) statBacklog.textContent = stats.backlog_count || 0;
    const statCompleted = document.getElementById("stat-completed");
    if (statCompleted) statCompleted.textContent = stats.completed_count || 0;

    updateReconcileButtonBadge(stats.reconcile_candidates_count || 0);
    renderPlaytimePrompts(stats.playtime_prompts || []);

    const genreSelect = document.getElementById("genre-filter");
    if (Array.isArray(stats.all_genres)) {
      const merged = new Set([...COMMON_GENRES, ...stats.all_genres]);
      state.allGenres = Array.from(merged).sort((a, b) => a.localeCompare(b));
      if (genreSelect) {
        const prevGenre = state.genre || genreSelect.value || "";
        genreSelect.innerHTML = "";
        const allOpt = document.createElement("option");
        allOpt.value = "";
        allOpt.textContent = "All Genres";
        genreSelect.appendChild(allOpt);
        for (const gName of stats.all_genres) {
          const opt = document.createElement("option");
          opt.value = gName;
          opt.textContent = gName;
          if (gName === prevGenre) opt.selected = true;
          genreSelect.appendChild(opt);
        }
      }
    }

    const tagSelect = document.getElementById("tag-filter");
    if (tagSelect && Array.isArray(stats.all_tags)) {
      const mergedTags = new Set([...COMMON_TAGS, ...stats.all_tags]);
      state.allTags = Array.from(mergedTags).sort((a, b) => a.localeCompare(b));
      const prevTag = state.tag || tagSelect.value || "";
      tagSelect.innerHTML = "";
      const allOpt = document.createElement("option");
      allOpt.value = "";
      allOpt.textContent = "All Tags";
      tagSelect.appendChild(allOpt);
      const tagList = stats.all_tags.length ? stats.all_tags : state.allTags;
      for (const tName of tagList) {
        const opt = document.createElement("option");
        opt.value = tName;
        opt.textContent = tName;
        if (tName === prevTag) opt.selected = true;
        tagSelect.appendChild(opt);
      }
    }

    const srcEl = document.getElementById("source-indicators");
    if (srcEl) {
      srcEl.innerHTML = "";
    }
    const cfg = stats.configured_sources || {};
    const activeKeys = Object.entries(cfg)
      .filter(([_, v]) => Boolean(v))
      .map(([k]) => k.toUpperCase());
    const syncBtn = document.getElementById("btn-sync");
    const syncDot = document.getElementById("sync-health-dot");
    if (syncBtn) {
      if (activeKeys.length > 0) {
        syncBtn.classList.add("sync-healthy");
        syncBtn.title = `Sync Health: Active (${activeKeys.join(", ")}) · Click to sync libraries`;
        if (syncDot) syncDot.classList.add("dot-active");
      } else {
        syncBtn.classList.remove("sync-healthy");
        syncBtn.title = "Sync Health: No platforms configured · Click to sync";
        if (syncDot) syncDot.classList.remove("dot-active");
      }
    }
  } catch (err) {
    console.error("Failed to fetch stats:", err);
  }
}

async function fetchGames() {
  const params = new URLSearchParams();
  if (state.search) params.set("search", state.search);
  if (state.platform) params.set("platform", state.platform);
  if (state.status) params.set("status", state.status);
  if (state.ownership) params.set("ownership", state.ownership);
  if (state.gamePassOnly) params.set("is_game_pass", "true");
  if (state.genre) params.set("genre", state.genre);
  if (state.tag) params.set("tag", state.tag);
  if (state.favoriteOnly) params.set("favorite_only", "true");
  params.set("sort_by", state.sortBy);

  try {
    const res = await fetch(`/api/games?${params.toString()}`);
    if (res.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
      return;
    }
    if (!res.ok) return;
    const data = await res.json();
    state.games = data.games || [];
    updateTriageButtonBadge();
    renderGames();
  } catch (err) {
    console.error("Failed to load games:", err);
  }
}

async function quickPatchGame(gameId, updates) {
  try {
    const res = await fetch(`/api/games/${gameId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!res.ok) return null;
    const data = await res.json();
    const updated = data.game;
    if (updated) {
      const targetGameId = updated.id || gameId;
      const idx = state.games.findIndex((g) => g.id === targetGameId || g.id === gameId);
      if (idx !== -1) state.games[idx] = updated;
      const tIdx = state.triageQueue.findIndex((g) => g.id === targetGameId || g.id === gameId);
      if (tIdx !== -1) state.triageQueue[tIdx] = updated;
      updateTriageButtonBadge();
      fetchStats();
    }
    return updated;
  } catch (err) {
    console.error("Quick update failed:", err);
    return null;
  }
}

function renderGames() {
  const grid = document.getElementById("games-grid");
  const countEl = document.getElementById("results-count");
  countEl.textContent = state.games.length;
  grid.innerHTML = "";
  grid.classList.toggle("list-view", state.viewMode === "list");
  updateActiveFiltersUI();

  const emptyWelcome = document.getElementById("empty-library-welcome");
  if (emptyWelcome) {
    emptyWelcome.hidden = state.games.length > 0;
  }

  if (state.games.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.innerHTML = `
      <p><strong>No games match your current filter.</strong></p>
      <p>Use <strong>+ Add Game</strong> to add Switch/console games, or configure your Steam, Xbox, and PSN keys in <code>.env</code> and click <strong>Sync Libraries</strong>.</p>
    `;
    grid.appendChild(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  for (let i = 0; i < state.games.length; i++) {
    const game = state.games[i];
    const card = document.createElement("article");
    card.className = i >= 12 ? "game-card deferred-card" : "game-card";
    card.tabIndex = 0;

    const fallbackSrc = makeFallbackCover(game.title, game.platform);
    const imgSrc = game.cover_url || fallbackSrc;

    const wrapper = document.createElement("div");
    wrapper.className = "cover-wrapper";

    const img = document.createElement("img");
    img.alt = `${game.title} cover art`;
    img.width = 300;
    img.height = 450;
    img.loading = i < 6 ? "eager" : "lazy";
    img.decoding = "async";
    attachCoverAspectObserver(wrapper, img, fallbackSrc, imgSrc);
    wrapper.appendChild(img);

    // Ultra-clean media catalog: Zero stacked badges across the face of the art!
    // Title, platform indicator, and completion state cleanly beneath each poster in high-contrast typography.
    const body = document.createElement("div");
    body.className = "card-body";

    const h3 = document.createElement("h3");
    h3.className = "card-title";
    h3.textContent = game.title;
    body.appendChild(h3);

    const ownStatus = game.ownership_status || "Owned";
    const metaRow = document.createElement("div");
    metaRow.className = "card-meta-row";

    const platSpan = document.createElement("span");
    platSpan.className = "card-plat-indicator";
    const brandIcons = createPlatformBrandIconsSpan(game);
    if (brandIcons) platSpan.appendChild(brandIcons);
    const platText = document.createElement("span");
    platText.className = "card-plat-text";
    platText.textContent = game.platform;
    platSpan.appendChild(platText);
    metaRow.appendChild(platSpan);

    if (game.is_game_pass) {
      const gpSpan = document.createElement("span");
      gpSpan.className = "card-gp-indicator";
      gpSpan.textContent = "Game Pass";
      metaRow.appendChild(gpSpan);
    } else if (ownStatus !== "Owned") {
      const ownSpan = document.createElement("span");
      const ownSlug = ownStatus.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      ownSpan.className = `card-ownership-indicator ${ownSlug}`;
      ownSpan.textContent = ownStatus;
      metaRow.appendChild(ownSpan);
    }

    if (game.playtime_hours > 0) {
      const hoursSpan = document.createElement("span");
      hoursSpan.className = "card-time-indicator";
      hoursSpan.innerHTML = `${LUCIDE_ICONS.clock} <span>${game.playtime_hours}h</span>`;
      metaRow.appendChild(hoursSpan);
    }
    body.appendChild(metaRow);

    const statusRow = document.createElement("div");
    statusRow.className = "card-status-row";

    const statusSelect = document.createElement("select");
    statusSelect.className = "card-quick-status";
    const currentStatus = game.completion_status || "Not Played";
    statusSelect.dataset.status = currentStatus;
    statusSelect.setAttribute("aria-label", `Completion status for ${game.title}`);
    for (const optVal of STATUS_OPTIONS) {
      const opt = document.createElement("option");
      opt.value = optVal;
      opt.textContent = optVal;
      if (optVal === currentStatus) opt.selected = true;
      statusSelect.appendChild(opt);
    }
    statusSelect.addEventListener("click", (e) => e.stopPropagation());
    statusSelect.addEventListener("keydown", (e) => e.stopPropagation());
    statusSelect.addEventListener("change", async (e) => {
      e.stopPropagation();
      const newStatus = e.target.value;
      statusSelect.dataset.status = newStatus;
      game.completion_status = newStatus;
      game.status_locked = true;
      await quickPatchGame(game.id, { completion_status: newStatus });
      if (state.status && state.status.toLowerCase() !== newStatus.toLowerCase()) {
        fetchGames();
      }
    });
    statusRow.appendChild(statusSelect);

    if (game.favorite) {
      const favSpan = document.createElement("span");
      favSpan.className = "card-fav-indicator";
      favSpan.innerHTML = LUCIDE_ICONS.starFilled;
      favSpan.title = "Favorited";
      statusRow.appendChild(favSpan);
    }

    body.appendChild(statusRow);

    card.appendChild(wrapper);
    card.appendChild(body);

    card.addEventListener("click", () => openEditModal(game));
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openEditModal(game);
      }
    });

    fragment.appendChild(card);
  }
  grid.appendChild(fragment);
}

function renderEditGenreChips() {
  const chipsEl = document.getElementById("edit-genre-chips");
  const inputEl = document.getElementById("edit-genres");
  const pickerEl = document.getElementById("edit-genre-picker");
  if (!chipsEl || !inputEl) return;
  chipsEl.innerHTML = "";
  const list = inputEl.value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  for (const gName of list) {
    const chip = document.createElement("span");
    chip.className = "edit-genre-chip";
    chip.textContent = gName;

    const rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.textContent = "✕";
    rmBtn.setAttribute("aria-label", `Remove ${gName}`);
    rmBtn.addEventListener("click", () => {
      const updated = list.filter((item) => item !== gName);
      inputEl.value = updated.join(", ");
      renderEditGenreChips();
    });
    chip.appendChild(rmBtn);
    chipsEl.appendChild(chip);
  }

  if (pickerEl) {
    const existingLower = new Set(list.map((g) => g.toLowerCase()));
    pickerEl.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "+ Add genre...";
    pickerEl.appendChild(placeholder);

    for (const gOption of state.allGenres) {
      if (existingLower.has(gOption.toLowerCase())) continue;
      const opt = document.createElement("option");
      opt.value = gOption;
      opt.textContent = gOption;
      pickerEl.appendChild(opt);
    }
    pickerEl.value = "";
  }
}

function renderEditTagChips() {
  const chipsEl = document.getElementById("edit-tag-chips");
  const inputEl = document.getElementById("edit-tags");
  const pickerEl = document.getElementById("edit-tag-picker");
  if (!chipsEl || !inputEl) return;
  chipsEl.innerHTML = "";
  const list = inputEl.value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  for (const tName of list) {
    const chip = document.createElement("span");
    chip.className = "edit-genre-chip";
    chip.textContent = tName;

    const rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.textContent = "✕";
    rmBtn.setAttribute("aria-label", `Remove ${tName}`);
    rmBtn.addEventListener("click", () => {
      const updated = list.filter((item) => item.toLowerCase() !== tName.toLowerCase());
      inputEl.value = updated.join(", ");
      renderEditTagChips();
    });
    chip.appendChild(rmBtn);
    chipsEl.appendChild(chip);
  }

  if (pickerEl) {
    const existingLower = new Set(list.map((t) => t.toLowerCase()));
    pickerEl.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "+ Add tag...";
    pickerEl.appendChild(placeholder);

    for (const tOption of state.allTags || COMMON_TAGS) {
      if (existingLower.has(tOption.toLowerCase())) continue;
      const opt = document.createElement("option");
      opt.value = tOption;
      opt.textContent = tOption;
      pickerEl.appendChild(opt);
    }
    pickerEl.value = "";
  }
}

function renderEditRatingPills(selectedVal) {
  const container = document.getElementById("edit-rating-pills");
  const inputEl = document.getElementById("edit-rating");
  const clearBtn = document.getElementById("btn-clear-rating");
  if (!container || !inputEl) return;
  container.innerHTML = "";
  const numVal = selectedVal ? parseInt(selectedVal, 10) : null;
  inputEl.value = numVal || "";
  if (clearBtn) {
    clearBtn.hidden = !numVal;
  }

  for (let i = 1; i <= 10; i++) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `rating-pill ${numVal === i ? "active" : ""}`;
    btn.textContent = String(i);
    btn.setAttribute("aria-pressed", String(numVal === i));
    btn.addEventListener("click", () => {
      const nextVal = numVal === i ? null : i;
      renderEditRatingPills(nextVal);
    });
    container.appendChild(btn);
  }
}

function syncEditFavButton(isFav) {
  const favCheck = document.getElementById("edit-favorite");
  const favBtn = document.getElementById("btn-edit-fav");
  if (favCheck) favCheck.checked = Boolean(isFav);
  if (favBtn) {
    favBtn.setAttribute("aria-pressed", String(Boolean(isFav)));
    favBtn.textContent = isFav ? "★ Favorited" : "☆ Favorite";
  }
}

function renderEditLinkedCopies(game) {
  const listEl = document.getElementById("edit-linked-list");
  const summaryEl = document.getElementById("edit-linked-summary");
  const hoursInput = document.getElementById("edit-hours");
  const hoursDisplay = document.getElementById("edit-hours-display");
  const hoursEditWrap = document.getElementById("edit-hours-edit-wrap");
  const toggleHoursBtn = document.getElementById("btn-toggle-edit-hours");
  const hoursHint = document.getElementById("edit-hours-hint");
  const searchInput = document.getElementById("edit-link-search");
  const resultsEl = document.getElementById("edit-link-results");

  if (!listEl) return;
  listEl.innerHTML = "";
  searchInput.value = "";
  resultsEl.hidden = true;
  resultsEl.innerHTML = "";

  const linked = Array.isArray(game.linked_games) && game.linked_games.length > 0
    ? game.linked_games
    : [game];

  const totalHours = game.playtime_hours || 0;
  if (hoursDisplay) {
    hoursDisplay.textContent = `⏱ ${totalHours} hrs`;
    hoursDisplay.hidden = false;
  }
  if (hoursEditWrap) {
    hoursEditWrap.hidden = true;
  }
  if (hoursInput) {
    hoursInput.readOnly = true;
    hoursInput.dataset.unlocked = "false";
  }

  if (linked.length > 1) {
    summaryEl.textContent = `${linked.length} copies • ${totalHours} hrs total`;
    if (toggleHoursBtn) toggleHoursBtn.hidden = true;
    hoursHint.hidden = false;
  } else {
    summaryEl.textContent = `1 copy (${game.platform})`;
    if (toggleHoursBtn) {
      toggleHoursBtn.hidden = false;
      toggleHoursBtn.textContent = "Edit";
    }
    hoursHint.hidden = true;
  }

  for (const copy of linked) {
    const li = document.createElement("li");
    li.className = "linked-copy-row";

    const left = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = copy.title;
    const platSpan = document.createElement("span");
    platSpan.className = "reconcile-copy-plat";
    platSpan.style.marginInlineStart = "0.45rem";
    platSpan.textContent = copy.platform;
    left.appendChild(strong);
    left.appendChild(platSpan);

    const right = document.createElement("div");
    right.style.display = "flex";
    right.style.alignItems = "center";
    right.style.gap = "0.6rem";

    const ownSelect = document.createElement("select");
    ownSelect.className = "linked-copy-ownership-select";
    const copyOwn = copy.ownership_status || "Owned";
    ownSelect.dataset.ownership = copyOwn;
    ownSelect.setAttribute("aria-label", `Ownership for ${copy.title} on ${copy.platform}`);
    for (const oVal of ["Owned", "Subscription", "Borrowed", "None"]) {
      const opt = document.createElement("option");
      opt.value = oVal;
      opt.textContent = oVal;
      if (oVal === copyOwn) opt.selected = true;
      ownSelect.appendChild(opt);
    }
    ownSelect.addEventListener("change", async (ev) => {
      const nextOwn = ev.target.value;
      ownSelect.dataset.ownership = nextOwn;
      const updated = await quickPatchGame(copy.id, { ownership_status: nextOwn });
      if (updated) {
        await fetchGames();
        const refreshed = state.games.find((g) => g.id === game.id) || updated;
        if (refreshed) openEditModal(refreshed);
      }
    });
    right.appendChild(ownSelect);

    const gpWrap = document.createElement("label");
    gpWrap.className = "linked-copy-gp-wrap";
    gpWrap.style.display = "inline-flex";
    gpWrap.style.alignItems = "center";
    gpWrap.style.gap = "0.25rem";
    gpWrap.style.cursor = "pointer";
    gpWrap.title = "Toggle Xbox Game Pass availability for this copy";

    const gpCheck = document.createElement("input");
    gpCheck.type = "checkbox";
    gpCheck.checked = Boolean(copy.is_game_pass);
    gpCheck.setAttribute("aria-label", `Game Pass for ${copy.title} on ${copy.platform}`);
    gpCheck.addEventListener("change", async (ev) => {
      const nextGp = ev.target.checked;
      const updated = await quickPatchGame(copy.id, { is_game_pass: nextGp });
      if (updated) {
        await fetchGames();
        const refreshed = state.games.find((g) => g.id === game.id) || updated;
        if (refreshed) openEditModal(refreshed);
      }
    });

    const gpBadge = document.createElement("span");
    gpBadge.className = "badge-gp-mini";
    gpBadge.textContent = "GP";
    gpWrap.appendChild(gpCheck);
    gpWrap.appendChild(gpBadge);
    right.appendChild(gpWrap);

    const hrs = document.createElement("span");
    hrs.className = "reconcile-copy-hours";
    hrs.textContent = `⏱ ${copy.playtime_hours || 0}h`;
    right.appendChild(hrs);

    const copyTot = copy.achievements_total || 0;
    const copyUnl = copy.achievements_unlocked || 0;
    if (copyTot > 0 || copyUnl > 0) {
      const copyAch = document.createElement("span");
      copyAch.className = "linked-copy-ach-badge";
      if (copyTot > 0 && copyUnl >= copyTot) {
        copyAch.classList.add("ach-complete");
      }
      copyAch.textContent = copyTot > 0 ? `${copyUnl}/${copyTot}` : `${copyUnl}`;
      copyAch.title = `${copy.platform} achievements: ${copyUnl}/${copyTot}`;
      right.appendChild(copyAch);
    }

    if (linked.length > 1) {
      const unlinkBtn = document.createElement("button");
      unlinkBtn.type = "button";
      unlinkBtn.className = "btn btn-secondary btn-xs";
      unlinkBtn.textContent = "Unlink";
      unlinkBtn.addEventListener("click", async () => {
        const res = await fetch(`/api/reconcile/unlink/${copy.id}`, { method: "POST" });
        if (res.ok) {
          await fetchStats();
          await fetchGames();
          const refreshed = state.games.find((g) => g.id === game.id) || (await res.json()).game;
          if (refreshed) openEditModal(refreshed);
        }
      });
      right.appendChild(unlinkBtn);
    }

    li.appendChild(left);
    li.appendChild(right);
    listEl.appendChild(li);
  }
}

function shortPlatformLabel(platform) {
  const p = (platform || "").trim();
  const mapping = {
    "PC (Steam)": "Steam",
    "PC (Xbox)": "PC (Xbox)",
    "Xbox / PC": "Xbox/PC",
    "Xbox": "Xbox",
    "PlayStation 4": "PS4",
    "PlayStation 5": "PS5",
    "Nintendo Switch": "Switch",
    "PC (Other)": "PC",
  };
  return mapping[p] || p;
}

function renderAchievementsList() {
  const payload = state.currentGameAchievements || {};
  const listEl = document.getElementById("edit-achievements-list");
  const countBadge = document.getElementById("ach-explorer-count-badge");
  const overlapBadge = document.getElementById("ach-explorer-overlap-badge");
  const emptyEl = document.getElementById("ach-empty-state");
  const platPillsWrap = document.getElementById("ach-platform-filter-pills");

  if (!listEl) return;
  listEl.innerHTML = "";

  const unified = payload.unified_summary || { unlocked: 0, total: 0, percentage: 0, overlap_count: 0 };
  const perPlatform = payload.per_platform_summary || [];
  const achievements = payload.achievements || [];

  if (countBadge) {
    countBadge.textContent = `${unified.unlocked} / ${unified.total} (${unified.percentage}%)`;
  }

  if (overlapBadge) {
    if (unified.overlap_count > 0) {
      overlapBadge.hidden = false;
      overlapBadge.textContent = `★ ${unified.overlap_count} overlapping on multiple platforms`;
    } else {
      overlapBadge.hidden = true;
    }
  }

  const allCountEl = document.getElementById("ach-pill-all-count");
  const unlCountEl = document.getElementById("ach-pill-unl-count");
  const lckCountEl = document.getElementById("ach-pill-lck-count");
  if (allCountEl) allCountEl.textContent = achievements.length;
  if (unlCountEl) unlCountEl.textContent = achievements.filter((a) => a.unlocked).length;
  if (lckCountEl) lckCountEl.textContent = achievements.filter((a) => !a.unlocked).length;

  if (platPillsWrap) {
    if (perPlatform.length > 1) {
      platPillsWrap.hidden = false;
      platPillsWrap.innerHTML = "";

      const allPlatBtn = document.createElement("button");
      allPlatBtn.type = "button";
      allPlatBtn.className = `ach-pill ${state.currentAchPlatform === "all" ? "active" : ""}`;
      allPlatBtn.textContent = "All Platforms";
      allPlatBtn.addEventListener("click", () => {
        state.currentAchPlatform = "all";
        renderAchievementsList();
      });
      platPillsWrap.appendChild(allPlatBtn);

      for (const p of perPlatform) {
        const pBtn = document.createElement("button");
        pBtn.type = "button";
        pBtn.className = `ach-pill ${state.currentAchPlatform === p.platform ? "active" : ""}`;
        pBtn.textContent = `${p.short_platform} (${p.unlocked}/${p.total})`;
        pBtn.addEventListener("click", () => {
          state.currentAchPlatform = p.platform;
          renderAchievementsList();
        });
        platPillsWrap.appendChild(pBtn);
      }
    } else {
      platPillsWrap.hidden = true;
      platPillsWrap.innerHTML = "";
    }
  }

  const filterStatus = state.currentAchFilter || "all";
  const filterPlat = state.currentAchPlatform || "all";
  const searchQ = (state.currentAchSearch || "").toLowerCase().trim();

  const filtered = achievements.filter((a) => {
    if (filterStatus === "unlocked" && !a.unlocked) return false;
    if (filterStatus === "locked" && a.unlocked) return false;

    if (filterPlat !== "all") {
      const matchPlat = (a.platforms || []).some(
        (p) => p.platform === filterPlat || p.short_platform === filterPlat
      );
      if (!matchPlat) return false;
    }

    if (searchQ) {
      const nameMatch = (a.name || "").toLowerCase().includes(searchQ);
      const descMatch = (a.description || "").toLowerCase().includes(searchQ);
      if (!nameMatch && !descMatch) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    if (emptyEl) {
      emptyEl.hidden = false;
      if (achievements.length === 0) {
        emptyEl.textContent = "No achievements synced yet. Click '↻ Sync Achievements' above to check platform APIs.";
      } else {
        emptyEl.textContent = "No achievements match your filter.";
      }
    }
    return;
  }

  if (emptyEl) emptyEl.hidden = true;

  for (const a of filtered) {
    const li = document.createElement("li");
    li.className = `ach-card-item ${a.unlocked ? "unlocked" : "locked"}`;

    const iconWrap = document.createElement("div");
    iconWrap.className = "ach-card-icon";

    const iconImg = document.createElement("img");
    iconImg.className = "ach-icon-img";
    const fallbackSrc = makeFallbackAchIcon(a.name);
    const targetSrc = a.unlocked
      ? a.icon_url || a.icon_gray_url || fallbackSrc
      : a.icon_gray_url || a.icon_url || fallbackSrc;
    iconImg.src = targetSrc;
    iconImg.alt = "";
    iconImg.width = 48;
    iconImg.height = 48;
    iconImg.loading = "lazy";
    iconImg.onerror = () => {
      iconImg.src = fallbackSrc;
    };
    iconWrap.appendChild(iconImg);
    li.appendChild(iconWrap);

    const body = document.createElement("div");
    body.className = "ach-card-body";

    const topRow = document.createElement("div");
    topRow.className = "ach-card-top";

    const nameEl = document.createElement("strong");
    nameEl.className = "ach-card-name";
    nameEl.textContent = a.name;
    topRow.appendChild(nameEl);

    const metaTags = document.createElement("div");
    metaTags.className = "ach-meta-tags";

    if (a.reward_type && a.reward_value) {
      const rewTag = document.createElement("span");
      const rewClass = (a.reward_type || "").toLowerCase();
      rewTag.className = `ach-reward-tag ${rewClass}`;
      rewTag.textContent = a.reward_type === "Gamerscore" ? `${a.reward_value}G` : `${a.reward_type}`;
      metaTags.appendChild(rewTag);
    }
    if (a.rarity_pct != null) {
      const rarTag = document.createElement("span");
      rarTag.className = "ach-rarity-tag";
      rarTag.textContent = `${Math.round(a.rarity_pct * 10) / 10}%`;
      rarTag.title = `Global unlock rate: ${a.rarity_pct}%`;
      metaTags.appendChild(rarTag);
    }
    topRow.appendChild(metaTags);
    body.appendChild(topRow);

    const descEl = document.createElement("p");
    descEl.className = "ach-card-desc";
    const descText = a.description || (a.is_hidden && !a.unlocked ? "Hidden secret achievement" : "No description provided");
    descEl.textContent = descText;
    body.appendChild(descEl);

    if (Array.isArray(a.platforms) && a.platforms.length > 0) {
      const platRow = document.createElement("div");
      platRow.className = "ach-platform-badges";
      for (const p of a.platforms) {
        const pBadge = document.createElement("span");
        pBadge.className = `ach-plat-badge ${p.unlocked ? "unlocked" : "locked"}`;
        const check = p.unlocked ? "✓" : "Locked";
        pBadge.textContent = `${check} ${p.short_platform}`;
        if (p.unlocked && p.unlock_time) {
          const uDate = new Date(p.unlock_time);
          const dateStr = !Number.isNaN(uDate.getTime())
            ? uDate.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
            : p.unlock_time;
          pBadge.title = `Unlocked on ${p.platform}: ${dateStr}`;
        } else {
          pBadge.title = p.unlocked ? `Unlocked on ${p.platform}` : `Locked on ${p.platform}`;
        }
        platRow.appendChild(pBadge);
      }
      body.appendChild(platRow);
    }

    li.appendChild(body);
    listEl.appendChild(li);
  }
}

async function loadAndRenderGameAchievements(gameId, forceSync = false) {
  const section = document.getElementById("edit-achievements-section");
  const listEl = document.getElementById("edit-achievements-list");
  const countBadge = document.getElementById("ach-explorer-count-badge");
  const overlapBadge = document.getElementById("ach-explorer-overlap-badge");
  const loadingEl = document.getElementById("ach-loading-indicator");
  const emptyEl = document.getElementById("ach-empty-state");
  const platPillsWrap = document.getElementById("ach-platform-filter-pills");
  const refreshBtn = document.getElementById("btn-refresh-game-ach");

  if (!section || !listEl) return;

  state.currentAchFilter = "all";
  state.currentAchPlatform = "all";
  state.currentAchSearch = "";
  const searchInput = document.getElementById("ach-search-input");
  if (searchInput) searchInput.value = "";

  for (const pill of document.querySelectorAll(".ach-pill[data-ach-filter]")) {
    pill.classList.toggle("active", pill.dataset.achFilter === "all");
  }

  listEl.innerHTML = "";
  if (loadingEl) loadingEl.hidden = false;
  if (emptyEl) emptyEl.hidden = true;
  if (refreshBtn) {
    refreshBtn.disabled = true;
    refreshBtn.textContent = "Syncing...";
  }

  try {
    const url = forceSync
      ? `/api/games/${gameId}/achievements/sync`
      : `/api/games/${gameId}/achievements?auto_sync=true`;
    const res = await fetch(url, { method: forceSync ? "POST" : "GET" });
    if (!res.ok) {
      if (emptyEl) {
        emptyEl.textContent = "Failed to load achievements.";
        emptyEl.hidden = false;
      }
      return;
    }
    const data = await res.json();
    const payload = forceSync ? (data.achievements || {}) : data;

    if (payload && payload.unified_summary) {
      const u = payload.unified_summary.unlocked || 0;
      const t = payload.unified_summary.total || 0;
      const pct = payload.unified_summary.percentage != null
        ? payload.unified_summary.percentage
        : (t > 0 ? Math.round((u / t) * 100) : 0);

      const achDisplay = document.getElementById("edit-ach-display");
      if (achDisplay) {
        if (t > 0) {
          achDisplay.textContent = `${u} / ${t} (${pct}%)`;
        } else if (u > 0) {
          achDisplay.textContent = `${u} unlocked`;
        } else {
          achDisplay.textContent = "—";
        }
      }

      const editAchUnlInput = document.getElementById("edit-ach-unlocked");
      const editAchTotInput = document.getElementById("edit-ach-total");
      const editAchWrap = document.getElementById("edit-ach-edit-wrap");
      if (editAchUnlInput && editAchWrap && editAchWrap.hidden) {
        editAchUnlInput.value = u;
      }
      if (editAchTotInput && editAchWrap && editAchWrap.hidden) {
        editAchTotInput.value = t;
      }

      const achPills = document.getElementById("edit-ach-breakdown-pills");
      if (achPills && Array.isArray(payload.per_platform_summary) && payload.per_platform_summary.length > 1) {
        achPills.innerHTML = "";
        for (const p of payload.per_platform_summary) {
          const pill = document.createElement("span");
          pill.className = "edit-ach-pill";
          pill.textContent = `${p.short_platform}: ${p.unlocked}/${p.total}`;
          achPills.appendChild(pill);
        }
      }

      const gIdx = state.games.findIndex((g) => g.id === gameId);
      if (gIdx !== -1) {
        const prevUnl = state.games[gIdx].achievements_unlocked;
        const prevTot = state.games[gIdx].achievements_total;
        if (prevUnl !== u || prevTot !== t) {
          state.games[gIdx].achievements_unlocked = u;
          state.games[gIdx].achievements_total = t;
          renderGames();
        }
      }
    }

    state.currentGameAchievements = payload;
    renderAchievementsList();
  } catch (err) {
    console.error("Achievements load error:", err);
    if (emptyEl) {
      emptyEl.textContent = "Could not load achievements.";
      emptyEl.hidden = false;
    }
  } finally {
    if (loadingEl) loadingEl.hidden = true;
    if (refreshBtn) {
      refreshBtn.disabled = false;
      refreshBtn.textContent = "↻ Sync Achievements";
    }
  }
}

function openEditModal(game) {
  const dialog = document.getElementById("edit-game-dialog");
  document.getElementById("edit-modal-title").textContent = game.title;

  const badgesEl = document.getElementById("edit-header-badges");
  if (badgesEl) {
    badgesEl.innerHTML = "";
    const platBadge = document.createElement("span");
    platBadge.className = "edit-header-badge plat";
    const editBrands = createPlatformBrandIconsSpan(game);
    if (editBrands) platBadge.appendChild(editBrands);
    const platText = document.createElement("span");
    platText.textContent = game.platform;
    platBadge.appendChild(platText);
    badgesEl.appendChild(platBadge);

    if (game.is_game_pass) {
      const gpBadge = document.createElement("span");
      gpBadge.className = "edit-header-badge gp";
      gpBadge.innerHTML = `${BRAND_ICONS.gamepass} <span>On Game Pass</span>`;
      badgesEl.appendChild(gpBadge);
    }

    const ownStatus = game.ownership_status || "Owned";
    if (ownStatus !== "Owned") {
      const ownBadge = document.createElement("span");
      const ownSlug = ownStatus.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      ownBadge.className = `edit-header-badge ${ownSlug}`;
      ownBadge.textContent = ownStatus;
      badgesEl.appendChild(ownBadge);
    }

    if (game.last_played) {
      const lpDate = new Date(game.last_played);
      if (!Number.isNaN(lpDate.getTime())) {
        const lpBadge = document.createElement("span");
        lpBadge.className = "edit-header-badge";
        lpBadge.textContent = `Last played ${lpDate.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
        badgesEl.appendChild(lpBadge);
      }
    }
  }

  const fallbackSrc = makeFallbackCover(game.title, game.platform);
  const coverPreview = document.getElementById("edit-cover-preview");
  const coverWrap = coverPreview ? coverPreview.closest(".cover-wrapper") : null;
  if (coverPreview && coverWrap) {
    attachCoverAspectObserver(coverWrap, coverPreview, fallbackSrc, game.cover_url || fallbackSrc);
  }

  const achTile = document.getElementById("edit-ach-tile");
  const achDisplay = document.getElementById("edit-ach-display");
  const achPills = document.getElementById("edit-ach-breakdown-pills");
  if (achTile && achDisplay) {
    const unl = game.achievements_unlocked || 0;
    const tot = game.achievements_total || 0;
    if (tot > 0) {
      const pct = Math.round((unl / tot) * 100);
      achDisplay.textContent = `${unl} / ${tot} (${pct}%)`;
      achTile.hidden = false;
    } else if (unl > 0) {
      achDisplay.textContent = `${unl} unlocked`;
      achTile.hidden = false;
    } else {
      achDisplay.textContent = "—";
      achTile.hidden = false;
    }

    if (achPills) {
      achPills.innerHTML = "";
      if (game.is_reconciled && Array.isArray(game.linked_games) && game.linked_games.length > 1) {
        for (const lg of game.linked_games) {
          const pill = document.createElement("span");
          pill.className = "edit-ach-pill";
          pill.textContent = `${shortPlatformLabel(lg.platform)}: ${lg.achievements_unlocked || 0}/${lg.achievements_total || 0}`;
          achPills.appendChild(pill);
        }
      }
    }
  }

  const editAchUnlInput = document.getElementById("edit-ach-unlocked");
  const editAchTotInput = document.getElementById("edit-ach-total");
  const editAchWrap = document.getElementById("edit-ach-edit-wrap");
  const toggleAchBtn = document.getElementById("btn-toggle-edit-ach");
  if (editAchUnlInput) editAchUnlInput.value = game.achievements_unlocked ?? 0;
  if (editAchTotInput) editAchTotInput.value = game.achievements_total ?? 0;
  if (editAchWrap) editAchWrap.hidden = true;
  if (achDisplay) achDisplay.hidden = false;
  if (toggleAchBtn) toggleAchBtn.textContent = "Edit";

  const metaTile = document.getElementById("edit-meta-tile");
  const metaDisplay = document.getElementById("edit-meta-display");
  if (metaTile && metaDisplay) {
    const parts = [];
    if (game.release_year) parts.push(String(game.release_year));
    if (game.critic_score) parts.push(`Metacritic ${game.critic_score}`);
    if (parts.length > 0) {
      metaDisplay.textContent = parts.join(" • ");
      metaTile.hidden = false;
    } else {
      metaTile.hidden = true;
    }
  }

  document.getElementById("edit-id").value = game.id;
  document.getElementById("edit-status").value = game.completion_status || "Not Played";
  const editOwnEl = document.getElementById("edit-ownership");
  const editOwnGroup = editOwnEl ? editOwnEl.closest(".form-group") : null;
  const isMultiCopy = game.is_reconciled && Array.isArray(game.linked_games) && game.linked_games.length > 1;
  if (editOwnEl) {
    editOwnEl.value = game.ownership_status || "Owned";
    editOwnEl.disabled = isMultiCopy;
    if (editOwnGroup) {
      editOwnGroup.title = isMultiCopy
        ? "Ownership is managed per linked copy in the Cross-Platform Copies section below."
        : "";
    }
  }
  const editGpEl = document.getElementById("edit-gamepass");
  const editGpGroup = editGpEl ? editGpEl.closest(".form-group") : null;
  if (editGpEl) {
    editGpEl.checked = Boolean(game.is_game_pass);
    editGpEl.disabled = isMultiCopy;
    if (editGpGroup) {
      editGpGroup.title = isMultiCopy
        ? "Game Pass availability is managed per linked copy in the Cross-Platform Copies section below."
        : "";
    }
  }
  document.getElementById("edit-hours").value = game.playtime_hours ?? 0;
  renderEditRatingPills(game.user_rating);
  document.getElementById("edit-genres").value = (game.genres || []).join(", ");
  renderEditGenreChips();
  const editTagsEl = document.getElementById("edit-tags");
  if (editTagsEl) {
    editTagsEl.value = (game.tags || []).join(", ");
    renderEditTagChips();
  }
  document.getElementById("edit-cover").value = game.cover_url || "";
  document.getElementById("edit-notes").value = game.notes || "";
  syncEditFavButton(game.favorite);
  renderEditLinkedCopies(game);
  loadAndRenderGameAchievements(game.id, false);
  if (!dialog.open) {
    dialog.showModal();
  }
  dialog.scrollTop = 0;
}

let reconcileToastTimer = null;

function showReconcileToast(msg) {
  const el = document.getElementById("reconcile-toast");
  if (!el) return;
  el.textContent = msg;
  clearTimeout(reconcileToastTimer);
  reconcileToastTimer = setTimeout(() => {
    el.textContent = "";
  }, 2400);
}

async function openReconcileModal() {
  document.getElementById("library-actions-dialog")?.close();
  const dialog = document.getElementById("reconcile-dialog");
  const scopeSelect = document.getElementById("reconcile-scope");
  state.reconcileScope = scopeSelect.value || "all";

  if (!dialog.open) {
    dialog.showModal();
  }
  dialog.scrollTop = 0;
  await reloadReconcileQueue(state.reconcileScope);
}

async function reloadReconcileQueue(scope) {
  state.reconcileScope = scope;
  const stageEl = document.getElementById("reconcile-stage");
  const manualStageEl = document.getElementById("reconcile-manual-stage");
  const progressBar = document.getElementById("reconcile-progress-bar");
  const prevBtn = document.getElementById("btn-reconcile-prev");
  const skipBtn = document.getElementById("btn-reconcile-skip");
  const counterEl = document.getElementById("reconcile-counter");

  if (scope === "manual") {
    stageEl.hidden = true;
    manualStageEl.hidden = false;
    progressBar.hidden = true;
    prevBtn.hidden = true;
    skipBtn.hidden = true;
    counterEl.textContent = "Manual";
    await renderManualReconcileStage();
    return;
  }

  stageEl.hidden = false;
  manualStageEl.hidden = true;
  progressBar.hidden = false;
  prevBtn.hidden = false;
  skipBtn.hidden = false;

  const crossOnly = scope === "cross_platform" ? "true" : "false";
  try {
    const res = await fetch(`/api/reconcile/candidates?cross_platform_only=${crossOnly}`);
    if (res.ok) {
      const data = await res.json();
      state.reconcileQueue = data.candidates || [];
      if (scope === "all") {
        updateReconcileButtonBadge(state.reconcileQueue.length);
      }
    }
  } catch (err) {
    console.error("Failed to load reconcile candidates:", err);
  }

  state.reconcileIndex = 0;
  initReconcileGroupSelection();
  renderReconcileCard();
}

function initReconcileGroupSelection() {
  const group = state.reconcileQueue[state.reconcileIndex];
  state.reconcileCheckedIds = new Set();
  if (!group || !Array.isArray(group.games)) return;
  for (const g of group.games) {
    state.reconcileCheckedIds.add(g.id);
  }
  const titleInput = document.getElementById("reconcile-unified-title");
  const statusSelect = document.getElementById("reconcile-unified-status");
  if (titleInput) titleInput.value = group.suggested_title || "";
  if (statusSelect) statusSelect.value = group.suggested_status || "Not Played";
}

function updateReconcilePreviewMetrics() {
  const group = state.reconcileQueue[state.reconcileIndex];
  if (!group) return;
  const selectedGames = (group.games || []).filter((g) => state.reconcileCheckedIds.has(g.id));
  const totalHours = Math.round(
    selectedGames.reduce((acc, g) => acc + (parseFloat(g.playtime_hours) || 0), 0) * 10
  ) / 10;

  const breakdownParts = selectedGames.map((g) => `${g.playtime_hours || 0}h`);
  const hoursText =
    selectedGames.length > 1
      ? `${breakdownParts.join(" + ")} = ${totalHours} hrs`
      : `${totalHours} hrs`;

  document.getElementById("reconcile-total-hours").textContent = hoursText;

  const plats = [];
  for (const g of selectedGames) {
    if (!plats.includes(g.platform)) plats.push(g.platform);
  }
  document.getElementById("reconcile-unified-plat").textContent =
    plats.join(" + ") || "Select at least 2 copies";

  document.getElementById("reconcile-selected-count").textContent =
    `${selectedGames.length} of ${group.games.length} selected`;

  const confirmBtn = document.getElementById("btn-reconcile-confirm");
  if (confirmBtn) {
    confirmBtn.disabled = selectedGames.length < 2;
  }
}

function renderReconcileCard() {
  const queue = state.reconcileQueue;
  const idx = state.reconcileIndex;
  const total = queue.length;

  const counterEl = document.getElementById("reconcile-counter");
  const progressFill = document.getElementById("reconcile-progress-fill");
  const cardEl = document.getElementById("reconcile-card");
  const emptyEl = document.getElementById("reconcile-empty");
  const prevBtn = document.getElementById("btn-reconcile-prev");
  const skipBtn = document.getElementById("btn-reconcile-skip");

  if (total === 0 || idx >= total) {
    counterEl.textContent = `${total} / ${total}`;
    progressFill.style.inlineSize = "100%";
    cardEl.hidden = true;
    emptyEl.hidden = false;
    prevBtn.disabled = total === 0 || idx <= 0;
    skipBtn.disabled = true;
    return;
  }

  cardEl.hidden = false;
  emptyEl.hidden = true;
  prevBtn.disabled = idx <= 0;
  skipBtn.disabled = false;

  counterEl.textContent = `${idx + 1} / ${total}`;
  const pct = Math.round((idx / total) * 100);
  progressFill.style.inlineSize = `${pct}%`;

  const group = queue[idx];
  const gridEl = document.getElementById("reconcile-candidates-grid");
  gridEl.innerHTML = "";

  for (const g of group.games) {
    const isChecked = state.reconcileCheckedIds.has(g.id);
    const copyCard = document.createElement("div");
    copyCard.className = `reconcile-copy-card ${isChecked ? "selected" : ""}`;

    const fallbackSrc = makeFallbackCover(g.title, g.platform);
    const thumb = document.createElement("img");
    thumb.className = "reconcile-copy-thumb";
    thumb.src = g.cover_url || fallbackSrc;
    thumb.alt = "";
    thumb.width = 64;
    thumb.height = 96;
    thumb.onerror = () => {
      thumb.src = fallbackSrc;
    };
    copyCard.appendChild(thumb);

    const info = document.createElement("div");
    info.className = "reconcile-copy-info";

    const topRow = document.createElement("div");
    topRow.className = "reconcile-copy-top";

    const titleEl = document.createElement("h4");
    titleEl.className = "reconcile-copy-title";
    titleEl.textContent = g.title;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "reconcile-copy-check";
    checkbox.checked = isChecked;
    checkbox.setAttribute("aria-label", `Include ${g.title} on ${g.platform}`);

    topRow.appendChild(titleEl);
    topRow.appendChild(checkbox);
    info.appendChild(topRow);

    const metaRow = document.createElement("div");
    metaRow.className = "reconcile-copy-meta";

    const platBadge = document.createElement("span");
    platBadge.className = "reconcile-copy-plat";
    platBadge.textContent = g.platform;
    metaRow.appendChild(platBadge);

    const statusBadge = document.createElement("span");
    statusBadge.className = "status-pill";
    const st = g.completion_status || "Not Played";
    statusBadge.dataset.status = st;
    statusBadge.textContent = st;
    metaRow.appendChild(statusBadge);

    info.appendChild(metaRow);

    const statsRow = document.createElement("div");
    statsRow.className = "reconcile-copy-meta";

    const hrsEl = document.createElement("span");
    hrsEl.className = "reconcile-copy-hours";
    hrsEl.textContent = `${g.playtime_hours || 0} hrs`;
    statsRow.appendChild(hrsEl);

    if ((g.achievements_total || 0) > 0 || (g.achievements_unlocked || 0) > 0) {
      const achEl = document.createElement("span");
      achEl.textContent = `${g.achievements_unlocked || 0}/${g.achievements_total || 0} ach`;
      statsRow.appendChild(achEl);
    }

    if (g.last_played) {
      const lpEl = document.createElement("span");
      lpEl.textContent = `Played ${formatLastPlayed(g.last_played)}`;
      statsRow.appendChild(lpEl);
    }

    info.appendChild(statsRow);
    copyCard.appendChild(info);

    const toggleSelect = () => {
      if (state.reconcileCheckedIds.has(g.id)) {
        state.reconcileCheckedIds.delete(g.id);
      } else {
        state.reconcileCheckedIds.add(g.id);
      }
      renderReconcileCard();
    };

    copyCard.addEventListener("click", (e) => {
      if (e.target === checkbox) return;
      toggleSelect();
    });
    checkbox.addEventListener("change", toggleSelect);

    gridEl.appendChild(copyCard);
  }

  updateReconcilePreviewMetrics();
}

async function confirmCurrentReconcileGroup() {
  const queue = state.reconcileQueue;
  if (state.reconcileIndex >= queue.length) return;
  const group = queue[state.reconcileIndex];
  const selectedIds = (group.games || [])
    .filter((g) => state.reconcileCheckedIds.has(g.id))
    .map((g) => g.id);

  if (selectedIds.length < 2) {
    showReconcileToast("Select at least 2 copies to consolidate.");
    return;
  }

  const customTitle = document.getElementById("reconcile-unified-title").value.trim();
  const unifiedStatus = document.getElementById("reconcile-unified-status").value;

  const res = await fetch("/api/reconcile/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      game_ids: selectedIds,
      primary_id: selectedIds[0],
      custom_title: customTitle || group.suggested_title,
      completion_status: unifiedStatus,
    }),
  });

  if (res.ok) {
    const data = await res.json();
    const merged = data.game;
    if (data.stats) {
      updateReconcileButtonBadge(data.stats.reconcile_candidates_count || 0);
    }
    showReconcileToast(
      `Consolidated "${merged.title}" (${merged.playtime_hours} hrs across ${merged.linked_count} copies)`
    );
    runTriageTransition(() => {
      state.reconcileQueue.splice(state.reconcileIndex, 1);
      initReconcileGroupSelection();
      renderReconcileCard();
    }, "forward");
  }
}

async function dismissCurrentReconcileGroup() {
  const queue = state.reconcileQueue;
  if (state.reconcileIndex >= queue.length) return;
  const group = queue[state.reconcileIndex];
  const allIds = (group.games || []).map((g) => g.id);

  const res = await fetch("/api/reconcile/dismiss", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ game_ids: allIds }),
  });

  if (res.ok) {
    showReconcileToast(`Dismissed "${group.suggested_title}"`);
    updateReconcileButtonBadge(Math.max(0, state.reconcileCandidatesCount - 1));
    runTriageTransition(() => {
      state.reconcileQueue.splice(state.reconcileIndex, 1);
      initReconcileGroupSelection();
      renderReconcileCard();
    }, "forward");
  }
}

function stepReconcile(delta) {
  const nextIdx = state.reconcileIndex + delta;
  if (nextIdx < 0 || nextIdx > state.reconcileQueue.length) return;
  const direction = delta >= 0 ? "forward" : "backward";
  runTriageTransition(() => {
    state.reconcileIndex = nextIdx;
    initReconcileGroupSelection();
    renderReconcileCard();
  }, direction);
}

async function renderManualReconcileStage() {
  await fetchGames();
  renderManualSelectedBar();
  renderManualSearchResults(document.getElementById("manual-reconcile-search").value.trim());
  renderReconciledGroupsList();
}

function renderManualSelectedBar() {
  const bar = document.getElementById("manual-reconcile-selected");
  const selected = state.manualSelectedGames;
  if (selected.length === 0) {
    bar.hidden = true;
    bar.innerHTML = "";
    return;
  }

  bar.hidden = false;
  bar.innerHTML = "";

  const chipsWrap = document.createElement("div");
  chipsWrap.className = "manual-selected-chips";

  let totalHrs = 0;
  for (const g of selected) {
    totalHrs += parseFloat(g.playtime_hours) || 0;
    const chip = document.createElement("span");
    chip.className = "manual-chip";
    chip.textContent = `${g.title} (${g.platform} • ${g.playtime_hours || 0}h)`;
    const rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.textContent = "✕";
    rmBtn.setAttribute("aria-label", `Remove ${g.title}`);
    rmBtn.addEventListener("click", () => {
      state.manualSelectedGames = state.manualSelectedGames.filter((x) => x.id !== g.id);
      renderManualSelectedBar();
      renderManualSearchResults(document.getElementById("manual-reconcile-search").value.trim());
    });
    chip.appendChild(rmBtn);
    chipsWrap.appendChild(chip);
  }

  const rightWrap = document.createElement("div");
  rightWrap.style.display = "flex";
  rightWrap.style.alignItems = "center";
  rightWrap.style.gap = "0.65rem";

  const hrsLabel = document.createElement("strong");
  hrsLabel.className = "reconcile-hours-highlight";
  hrsLabel.textContent = `Total: ${Math.round(totalHrs * 10) / 10} hrs`;
  rightWrap.appendChild(hrsLabel);

  const linkBtn = document.createElement("button");
  linkBtn.type = "button";
  linkBtn.className = "btn btn-primary btn-xs";
  linkBtn.disabled = selected.length < 2;
  linkBtn.textContent = selected.length >= 2 ? `✓ Link ${selected.length} Games` : "Select 1 more game...";
  linkBtn.addEventListener("click", async () => {
    const ids = selected.map((x) => x.id);
    const res = await fetch("/api/reconcile/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ game_ids: ids, primary_id: ids[0] }),
    });
    if (res.ok) {
      const data = await res.json();
      state.manualSelectedGames = [];
      showReconcileToast(`Linked "${data.game.title}" (${data.game.playtime_hours} hrs)`);
      await fetchStats();
      await renderManualReconcileStage();
    }
  });
  rightWrap.appendChild(linkBtn);

  bar.appendChild(chipsWrap);
  bar.appendChild(rightWrap);
}

async function renderManualSearchResults(query) {
  const listEl = document.getElementById("manual-reconcile-results");
  listEl.innerHTML = "";
  const q = (query || "").toLowerCase().trim();

  let pool = state.games;
  if (q) {
    try {
      const res = await fetch(`/api/games?search=${encodeURIComponent(q)}&limit=40`);
      if (res.ok) {
        const data = await res.json();
        pool = data.games || [];
      }
    } catch (err) {
      console.error("Manual search error:", err);
    }
  } else {
    pool = state.games.slice(0, 25);
  }

  for (const g of pool) {
    const alreadySelected = state.manualSelectedGames.some((x) => x.id === g.id);
    const li = document.createElement("li");
    li.className = "manual-result-row";

    const left = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = g.title;
    const plat = document.createElement("span");
    plat.className = "reconcile-copy-plat";
    plat.style.marginInlineStart = "0.45rem";
    plat.textContent = g.platform;
    left.appendChild(strong);
    left.appendChild(plat);

    const right = document.createElement("div");
    right.style.display = "flex";
    right.style.alignItems = "center";
    right.style.gap = "0.6rem";

    const hrs = document.createElement("span");
    hrs.textContent = `⏱ ${g.playtime_hours || 0}h`;
    right.appendChild(hrs);

    const selectBtn = document.createElement("button");
    selectBtn.type = "button";
    selectBtn.className = `btn ${alreadySelected ? "btn-secondary" : "btn-primary"} btn-xs`;
    selectBtn.textContent = alreadySelected ? "Selected ✓" : "+ Select";
    selectBtn.addEventListener("click", () => {
      if (alreadySelected) {
        state.manualSelectedGames = state.manualSelectedGames.filter((x) => x.id !== g.id);
      } else {
        state.manualSelectedGames.push(g);
      }
      renderManualSelectedBar();
      renderManualSearchResults(query);
    });
    right.appendChild(selectBtn);

    li.appendChild(left);
    li.appendChild(right);
    listEl.appendChild(li);
  }
}

function renderReconciledGroupsList() {
  const container = document.getElementById("reconciled-groups-list");
  const countBadge = document.getElementById("reconciled-groups-count");
  container.innerHTML = "";

  const reconciled = state.games.filter((g) => g.is_reconciled && (g.linked_count || 1) > 1);
  countBadge.textContent = reconciled.length;

  if (reconciled.length === 0) {
    const empty = document.createElement("div");
    empty.className = "subtitle";
    empty.textContent = "No games have been linked yet. Use Suggested Matches or Manual Link above.";
    container.appendChild(empty);
    return;
  }

  for (const g of reconciled) {
    const card = document.createElement("div");
    card.className = "reconciled-group-card";

    const top = document.createElement("div");
    top.className = "reconciled-group-top";

    const titleWrap = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = g.title;
    const badge = document.createElement("span");
    badge.className = "linked-summary-badge";
    badge.style.marginInlineStart = "0.5rem";
    badge.textContent = `${g.linked_count} copies • ${g.playtime_hours || 0} hrs total`;
    titleWrap.appendChild(strong);
    titleWrap.appendChild(badge);
    top.appendChild(titleWrap);
    card.appendChild(top);

    const copiesList = document.createElement("ul");
    copiesList.className = "linked-copies-list";
    for (const lg of g.linked_games || []) {
      const row = document.createElement("li");
      row.className = "linked-copy-row";

      const lText = document.createElement("span");
      lText.textContent = `${lg.title} (${lg.platform}) — ⏱ ${lg.playtime_hours || 0}h`;

      const unlinkBtn = document.createElement("button");
      unlinkBtn.type = "button";
      unlinkBtn.className = "btn btn-secondary btn-xs";
      unlinkBtn.textContent = "Unlink";
      unlinkBtn.addEventListener("click", async () => {
        const res = await fetch(`/api/reconcile/unlink/${lg.id}`, { method: "POST" });
        if (res.ok) {
          showReconcileToast(`Unlinked ${lg.title} (${lg.platform})`);
          await fetchStats();
          await renderManualReconcileStage();
        }
      });

      row.appendChild(lText);
      row.appendChild(unlinkBtn);
      copiesList.appendChild(row);
    }
    card.appendChild(copiesList);
    container.appendChild(card);
  }
}

let triageToastTimer = null;

function showTriageToast(msg) {
  const el = document.getElementById("triage-toast");
  if (!el) return;
  el.textContent = msg;
  clearTimeout(triageToastTimer);
  triageToastTimer = setTimeout(() => {
    el.textContent = "";
  }, 2200);
}

function runTriageTransition(updateDOM, direction = "forward") {
  if (!document.startViewTransition) {
    updateDOM();
    return;
  }
  try {
    document.startViewTransition({
      update: updateDOM,
      types: [direction],
    });
  } catch {
    document.startViewTransition(updateDOM);
  }
}

function formatLastPlayed(isoStr) {
  if (!isoStr) return "Never";
  try {
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return "Never";
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return "Never";
  }
}

function buildTriageQueue(sourceGames, scope) {
  if (scope === "unlocked") {
    return sourceGames.filter((g) => !g.status_locked);
  }
  if (scope === "xbox_ownership") {
    return sourceGames.filter(
      (g) =>
        (g.platform || "").toLowerCase().includes("xbox") ||
        (g.sources || []).some((s) => s.toLowerCase().includes("xbox"))
    );
  }
  if (scope === "not_played") {
    return sourceGames.filter((g) => (g.completion_status || "Not Played") === "Not Played");
  }
  return [...sourceGames];
}

// -----------------------------------------------------------------------------
// Settings & Connections Modal + Data Portability
// -----------------------------------------------------------------------------

function switchSettingsTab(tabId) {
  const tabs = [
    { btn: "stab-connections", panel: "ssection-connections" },
    { btn: "stab-mcp", panel: "ssection-mcp" },
    { btn: "stab-portability", panel: "ssection-portability" },
    { btn: "stab-account", panel: "ssection-account" },
    { btn: "stab-admin-users", panel: "ssection-admin-users" },
  ];
  for (const t of tabs) {
    const isTarget = t.btn === tabId;
    const btnEl = document.getElementById(t.btn);
    const panelEl = document.getElementById(t.panel);
    if (btnEl) {
      btnEl.classList.toggle("active", isTarget);
      btnEl.setAttribute("aria-selected", isTarget ? "true" : "false");
    }
    if (panelEl) {
      panelEl.hidden = !isTarget;
    }
  }
  if (tabId === "stab-account") {
    loadAccountInfo();
  } else if (tabId === "stab-admin-users") {
    loadAdminUsers();
  }
}
window.switchSettingsTab = switchSettingsTab;

async function loadSettingsConnections() {
  const statusEl = document.getElementById("connections-status-msg");
  if (statusEl) statusEl.textContent = "";
  try {
    const res = await fetch("/api/user/connections");
    if (!res.ok) return;
    const data = await res.json();
    const steamInput = document.getElementById("setting-steam-id");
    const xblInput = document.getElementById("setting-xbl-key");
    const psnInput = document.getElementById("setting-psn-npsso");
    const inputRow = document.getElementById("setting-xbox-input-row");
    const card = document.getElementById("settings-xbox-connected-card");
    const gtEl = document.getElementById("settings-xbox-gamertag");
    const scoreEl = document.getElementById("settings-xbox-gamerscore");
    const avatarEl = document.getElementById("settings-xbox-avatar");

    if (steamInput) steamInput.value = data.steam_id || "";

    if (data.xbox_gamertag) {
      if (inputRow) inputRow.hidden = true;
      if (card) card.hidden = false;
      if (gtEl) gtEl.textContent = data.xbox_gamertag;
      if (xblInput) xblInput.value = data.xbox_gamertag;

      fetch("/api/user/connections/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: "xbox", credential: data.xbox_gamertag }),
      })
        .then((r) => r.json())
        .then((lookup) => {
          if (lookup.status === "ok") {
            if (avatarEl) {
              if (lookup.avatar_url) {
                avatarEl.src = lookup.avatar_url;
                avatarEl.style.display = "";
              } else {
                avatarEl.style.display = "none";
              }
            }
            if (scoreEl) {
              if (lookup.gamerscore !== undefined && lookup.gamerscore !== null) {
                scoreEl.textContent = "⭐ " + Number(lookup.gamerscore).toLocaleString() + " G";
                scoreEl.style.display = "";
              } else {
                scoreEl.style.display = "none";
              }
            }
          }
        })
        .catch(() => {});
    } else {
      if (inputRow) inputRow.hidden = false;
      if (card) card.hidden = true;
      if (xblInput) {
        xblInput.value = "";
        xblInput.placeholder = "Enter Xbox Gamertag (e.g. Chief117)";
      }
    }

    if (psnInput) {
      psnInput.value = "";
      psnInput.placeholder = data.has_psn_npsso
        ? (data.psn_npsso_masked || "Configured (enter new to change)")
        : "Enter 64-char token (leave blank to keep current)";
    }
  } catch (err) {
    console.error("Failed to load connections:", err);
  }
}

async function saveSettingsConnections() {
  const statusEl = document.getElementById("connections-status-msg");
  const steamInput = document.getElementById("setting-steam-id");
  const xblInput = document.getElementById("setting-xbl-key");
  const psnInput = document.getElementById("setting-psn-npsso");
  const xboxCard = document.getElementById("settings-xbox-connected-card");

  const payload = {
    steam_id: steamInput ? steamInput.value.trim() : "",
  };
  if (xblInput && (!xboxCard || xboxCard.hidden) && xblInput.value.trim()) {
    payload.xbox_gamertag = xblInput.value.trim();
  }
  if (psnInput && psnInput.value.trim()) {
    payload.psn_npsso = psnInput.value.trim();
  }

  if (statusEl) {
    statusEl.className = "settings-status-msg";
    statusEl.textContent = "Saving...";
  }

  try {
    const res = await fetch("/api/user/connections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      if (statusEl) {
        statusEl.className = "settings-status-msg success";
        statusEl.textContent = "✓ Connections saved!";
      }
      loadSettingsConnections();
      fetchStats();
    } else {
      if (statusEl) {
        statusEl.className = "settings-status-msg error";
        statusEl.textContent = data.detail || "Failed to save connections";
      }
    }
  } catch (err) {
    if (statusEl) {
      statusEl.className = "settings-status-msg error";
      statusEl.textContent = "Error saving connections";
    }
  }
}

async function loadSettingsMcp() {
  const endpointInput = document.getElementById("mcp-endpoint-input");
  try {
    const res = await fetch("/api/user/mcp");
    if (!res.ok) return;
    const data = await res.json();
    if (endpointInput) {
      endpointInput.value = data.mcp_endpoint || "";
    }
    renderTokensList(data.tokens || []);
  } catch (err) {
    console.error("Failed to load MCP settings:", err);
  }
}

function renderTokensList(tokens) {
  const container = document.getElementById("tokens-list-container");
  if (!container) return;
  if (!tokens || tokens.length === 0) {
    container.innerHTML = '<div style="color:var(--text-secondary);font-size:0.85rem;padding:0.5rem 0;">No personal access tokens generated yet.</div>';
    return;
  }
  container.innerHTML = tokens
    .map((t) => {
      const dateStr = t.created_at
        ? new Date(t.created_at * 1000).toLocaleDateString()
        : "";
      return `
      <div class="token-item">
        <div class="token-item-info">
          <span class="token-item-label">${escapeHtml(t.client_id || "Personal Access Token")}</span>
          <span class="token-item-meta"><code>${escapeHtml(t.token_preview || "")}</code> &bull; Created ${dateStr}</span>
        </div>
        <button type="button" class="btn btn-sm btn-outline btn-revoke-token" data-token="${escapeHtml(t.access_token)}">
          Revoke
        </button>
      </div>
    `;
    })
    .join("");

  for (const btn of container.querySelectorAll(".btn-revoke-token")) {
    btn.addEventListener("click", async () => {
      const tok = btn.dataset.token;
      if (!confirm("Revoke this access token? Any AI client using it will lose access.")) return;
      try {
        const res = await fetch(`/api/user/mcp/tokens/${encodeURIComponent(tok)}`, { method: "DELETE" });
        if (res.ok) {
          loadSettingsMcp();
        }
      } catch (err) {
        console.error("Failed to revoke token:", err);
      }
    });
  }
}

async function generateMcpToken() {
  const labelInput = document.getElementById("token-label-input");
  const alertBox = document.getElementById("new-token-alert");
  const tokenValInput = document.getElementById("new-token-value");
  const label = labelInput ? labelInput.value.trim() : "";

  try {
    const res = await fetch("/api/user/mcp/tokens", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: label || "Personal Access Token" }),
    });
    if (!res.ok) return;
    const data = await res.json();
    if (tokenValInput) tokenValInput.value = data.token;
    if (alertBox) alertBox.hidden = false;
    if (labelInput) labelInput.value = "";
    loadSettingsMcp();
  } catch (err) {
    console.error("Failed to generate token:", err);
  }
}

async function handleDatabaseImport(file) {
  const banner = document.getElementById("import-status-banner");
  if (!file) return;

  if (
    !confirm(
      `Are you sure you want to replace your library with "${file.name}"?\n\nAn automatic safety backup of your existing library will be created before replacement.`
    )
  ) {
    return;
  }

  if (banner) {
    banner.className = "import-status-banner";
    banner.textContent = "Uploading and validating database...";
    banner.hidden = false;
  }

  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/user/library/import", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    if (res.ok) {
      if (banner) {
        banner.className = "import-status-banner success";
        banner.textContent = `✓ Library restored successfully (${data.imported_games || 0} games loaded)!`;
      }
      await fetchStats();
      await fetchGames();
    } else {
      if (banner) {
        banner.className = "import-status-banner error";
        banner.textContent = `Error: ${data.detail || "Database import failed"}`;
      }
    }
  } catch (err) {
    if (banner) {
      banner.className = "import-status-banner error";
      banner.textContent = `Failed to upload database: ${err.message}`;
    }
  }
}

async function loadAccountInfo() {
  try {
    const res = await fetch("/api/account");
    if (!res.ok) return;
    const data = await res.json();
    const userEl = document.getElementById("account-display-username");
    const roleEl = document.getElementById("account-display-role");
    const statusEl = document.getElementById("account-display-status");
    const adminTab = document.getElementById("stab-admin-users");

    if (userEl) userEl.textContent = data.username || "—";
    if (roleEl) {
      roleEl.textContent = data.role === "admin" ? "Administrator" : "User";
      roleEl.className = data.role === "admin" ? "badge-role badge-role-admin" : "badge-role badge-role-user";
    }
    if (statusEl) {
      statusEl.textContent = data.status || "active";
      statusEl.className = `badge-status badge-status-${data.status}`;
    }
    if (adminTab) {
      adminTab.hidden = data.role !== "admin";
    }
  } catch (err) {
    console.error("Failed to load account info:", err);
  }
}

async function saveAccountPassword() {
  const curInput = document.getElementById("input-current-password");
  const newInput = document.getElementById("input-new-password");
  const confInput = document.getElementById("input-confirm-password");
  const msgEl = document.getElementById("password-status-msg");

  if (!curInput || !newInput || !confInput || !msgEl) return;
  msgEl.className = "connections-status-msg";
  msgEl.textContent = "";

  if (!curInput.value) {
    msgEl.className = "connections-status-msg error";
    msgEl.textContent = "Please enter your current password.";
    return;
  }
  if (!newInput.value || newInput.value.length < 8) {
    msgEl.className = "connections-status-msg error";
    msgEl.textContent = "New password must be at least 8 characters.";
    return;
  }
  if (newInput.value !== confInput.value) {
    msgEl.className = "connections-status-msg error";
    msgEl.textContent = "New passwords do not match.";
    return;
  }

  try {
    const res = await fetch("/api/account/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: curInput.value,
        new_password: newInput.value,
      }),
    });
    const data = await res.json();
    if (res.ok) {
      msgEl.className = "connections-status-msg success";
      msgEl.textContent = "Password updated successfully.";
      curInput.value = "";
      newInput.value = "";
      confInput.value = "";
    } else {
      msgEl.className = "connections-status-msg error";
      msgEl.textContent = data.detail || "Failed to update password.";
    }
  } catch (err) {
    msgEl.className = "connections-status-msg error";
    msgEl.textContent = `Error: ${err.message}`;
  }
}

async function loadAdminSettings() {
  try {
    const res = await fetch("/api/admin/settings");
    if (!res.ok) return;
    const data = await res.json();
    const sel = document.getElementById("select-reg-policy");
    if (sel && data.registration_policy) {
      sel.value = data.registration_policy;
    }
  } catch (err) {
    console.error("Failed to load admin settings:", err);
  }
}

async function saveAdminRegPolicy() {
  const sel = document.getElementById("select-reg-policy");
  const msgEl = document.getElementById("reg-policy-status-msg");
  if (!sel || !msgEl) return;
  msgEl.className = "connections-status-msg";
  msgEl.textContent = "Saving...";

  try {
    const res = await fetch("/api/admin/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ registration_policy: sel.value }),
    });
    const data = await res.json();
    if (res.ok) {
      msgEl.className = "connections-status-msg success";
      msgEl.textContent = `Policy updated to "${data.registration_policy}".`;
      setTimeout(() => { msgEl.textContent = ""; }, 3000);
    } else {
      msgEl.className = "connections-status-msg error";
      msgEl.textContent = data.detail || "Failed to update policy.";
    }
  } catch (err) {
    msgEl.className = "connections-status-msg error";
    msgEl.textContent = `Error: ${err.message}`;
  }
}

async function loadAdminInvites() {
  const tbody = document.getElementById("admin-invites-tbody");
  if (!tbody) return;
  try {
    const res = await fetch("/api/admin/invites");
    if (!res.ok) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:1rem; color:#ef4444;">Failed to load invites (${res.status})</td></tr>`;
      return;
    }
    const data = await res.json();
    const invites = data.invites || [];
    if (!invites.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:1rem; color:#64748b;">No active or past invites.</td></tr>`;
      return;
    }
    tbody.innerHTML = invites.map((inv) => {
      const isUsed = inv.status === "used";
      const isExpired = inv.status === "expired";
      const statusClass = isUsed ? "badge-status-pending" : (isExpired ? "badge-status-inactive" : "badge-status-active");

      return `<tr>
        <td><code>${escapeHtml(inv.code_preview)}</code></td>
        <td style="font-size:0.8rem; color:#94a3b8;">${inv.created_at ? inv.created_at.split('T')[0] : '—'}</td>
        <td style="font-size:0.8rem; color:#94a3b8;">${inv.expires_at ? inv.expires_at.split('T')[0] : 'Never'}</td>
        <td><span class="badge-status ${statusClass}">${escapeHtml(inv.status)}</span></td>
        <td>
          ${!isUsed ? `
            <button type="button" class="btn btn-sm btn-danger-outline"
              onclick="revokeAdminInvite('${escapeHtml(inv.code_hash)}')">Revoke</button>
          ` : `<span style="font-size:0.8rem; color:#64748b;">by ${escapeHtml(inv.used_by || 'user')}</span>`}
        </td>
      </tr>`;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:1rem; color:#ef4444;">Error: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function createAdminInvite() {
  try {
    const res = await fetch("/api/admin/invites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ttl_days: 7 }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Failed to create invite: ${data.detail || "Error"}`);
      return;
    }
    const alertBox = document.getElementById("admin-new-invite-alert");
    const linkInput = document.getElementById("admin-invite-link-input");
    if (alertBox && linkInput) {
      const fullUrl = `${window.location.origin}${data.invite_url}`;
      linkInput.value = fullUrl;
      alertBox.hidden = false;
      try {
        await navigator.clipboard.writeText(fullUrl);
      } catch (e) {}
    } else {
      prompt("Copy your invite link:", `${window.location.origin}${data.invite_url}`);
    }
    loadAdminInvites();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function revokeAdminInvite(codeHash) {
  if (!confirm("Revoke this invite code?")) return;
  try {
    const res = await fetch(`/api/admin/invites/${encodeURIComponent(codeHash)}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const data = await res.json();
      alert(`Revoke failed: ${data.detail || "Error"}`);
      return;
    }
    loadAdminInvites();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}
window.revokeAdminInvite = revokeAdminInvite;

async function loadAdminUsers() {
  loadAdminSettings();
  loadAdminInvites();
  const tbody = document.getElementById("admin-users-tbody");

  if (!tbody) return;
  try {
    const res = await fetch("/api/admin/users");
    if (!res.ok) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:#ef4444;">Failed to load users (${res.status})</td></tr>`;
      return;
    }
    const data = await res.json();
    const users = data.users || [];
    if (!users.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:#64748b;">No users registered yet.</td></tr>`;
      return;
    }
    tbody.innerHTML = users.map((u) => {
      const isSelf = u.username === (document.getElementById("account-display-username")?.textContent || "");
      const isActive = u.status === "active";
      const isAdmin = u.role === "admin";
      const statusClass = isActive ? "badge-status-active" : "badge-status-inactive";
      const roleClass = isAdmin ? "badge-role-admin" : "badge-role-user";

      return `<tr>
        <td><strong>${escapeHtml(u.username)}</strong>${isSelf ? ' <span style="font-size:0.75rem; color:#38bdf8;">(you)</span>' : ''}</td>
        <td><span class="badge-role ${roleClass}">${isAdmin ? "Admin" : "User"}</span></td>
        <td><span class="badge-status ${statusClass}">${escapeHtml(u.status)}</span></td>
        <td style="font-size:0.8rem; color:#94a3b8;">${u.created_at ? u.created_at.split('T')[0] : '—'}</td>
        <td class="admin-actions-cell">
          ${!isSelf ? `
            <button type="button" class="btn btn-sm ${isActive ? 'btn-danger-outline' : 'btn-success-outline'}"
              onclick="updateAdminUserStatus('${escapeHtml(u.id)}', '${isActive ? 'inactive' : 'active'}', '${escapeHtml(u.username)}')">
              ${isActive ? 'Disable' : 'Enable'}
            </button>
            <button type="button" class="btn btn-sm btn-secondary"
              onclick="updateAdminUserRole('${escapeHtml(u.id)}', '${isAdmin ? 'user' : 'admin'}')">
              ${isAdmin ? 'Demote' : 'Make Admin'}
            </button>
          ` : ''}
          <button type="button" class="btn btn-sm btn-secondary"
            onclick="resetAdminUserPassword('${escapeHtml(u.id)}', '${escapeHtml(u.username)}')">
            Reset PW
          </button>
          ${!isSelf ? `
            <button type="button" class="btn btn-sm btn-danger-outline"
              onclick="openAdminDeleteUserDialog('${escapeHtml(u.id)}', '${escapeHtml(u.username)}')">
              Delete
            </button>
          ` : ''}
        </td>
      </tr>`;
    }).join("");
  } catch (err) {
    console.error("Failed to load admin users:", err);
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:1.5rem; color:#ef4444;">Error: ${escapeHtml(err.message)}</td></tr>`;
  }
}

async function updateAdminUserStatus(userId, newStatus, username) {
  if (newStatus === "inactive") {
    const userDisplay = username || userId;
    if (!confirm(`Are you sure you want to disable the account for "${userDisplay}"? The user will be signed out immediately and blocked from logging in until re-enabled.`)) {
      return;
    }
  }
  try {
    const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        status: newStatus,
        status_reason: newStatus === "inactive" ? "admin_suspended" : null,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Action failed: ${err.detail || "Could not update status"}`);
      return;
    }
    loadAdminUsers();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}
window.updateAdminUserStatus = updateAdminUserStatus;

let adminUserToDelete = null;

function openAdminDeleteUserDialog(userId, username) {
  adminUserToDelete = { id: userId, username: username };
  const dialog = document.getElementById("admin-delete-user-dialog");
  const targetDisplay = document.getElementById("delete-user-target-display");
  const confirmTarget = document.getElementById("delete-user-confirm-target");
  const confirmInput = document.getElementById("delete-user-confirm-input");
  const confirmBtn = document.getElementById("btn-confirm-delete-user");

  if (targetDisplay) targetDisplay.textContent = username;
  if (confirmTarget) confirmTarget.textContent = username;
  if (confirmInput) {
    confirmInput.value = "";
    confirmInput.oninput = () => {
      if (confirmBtn) {
        confirmBtn.disabled = confirmInput.value.trim() !== username;
      }
    };
    confirmInput.onkeydown = (e) => {
      if (e.key === "Enter" && confirmInput.value.trim() === username && !confirmBtn.disabled) {
        e.preventDefault();
        confirmAdminDeleteUser();
      }
    };
  }
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Permanently Delete Account";
  }

  if (dialog && typeof dialog.showModal === "function") {
    dialog.showModal();
  }
}
window.openAdminDeleteUserDialog = openAdminDeleteUserDialog;

function closeAdminDeleteUserDialog() {
  const dialog = document.getElementById("admin-delete-user-dialog");
  if (dialog && dialog.open) {
    dialog.close();
  }
  adminUserToDelete = null;
}
window.closeAdminDeleteUserDialog = closeAdminDeleteUserDialog;

async function confirmAdminDeleteUser() {
  if (!adminUserToDelete || !adminUserToDelete.id) return;
  const userId = adminUserToDelete.id;
  const confirmBtn = document.getElementById("btn-confirm-delete-user");

  try {
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Deleting...";
    }
    const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Deletion failed: ${err.detail || "Could not delete user"}`);
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Permanently Delete Account";
      }
      return;
    }
    closeAdminDeleteUserDialog();
    loadAdminUsers();
  } catch (e) {
    alert(`Error: ${e.message}`);
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Permanently Delete Account";
    }
  }
}
window.confirmAdminDeleteUser = confirmAdminDeleteUser;

async function updateAdminUserRole(userId, newRole) {
  try {
    const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: newRole }),
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Action failed: ${err.detail || "Could not update role"}`);
      return;
    }
    loadAdminUsers();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}
window.updateAdminUserRole = updateAdminUserRole;

async function resetAdminUserPassword(userId, username) {
  if (!confirm(`Reset password for user "${username}"? This will invalidate their current sessions and generate a new temporary password.`)) {
    return;
  }
  try {
    const res = await fetch(`/api/admin/users/${encodeURIComponent(userId)}/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Reset failed: ${data.detail || "Could not reset password"}`);
      return;
    }
    const alertBox = document.getElementById("admin-temp-pw-alert");
    const codeEl = document.getElementById("admin-temp-pw-display");
    if (alertBox && codeEl) {
      codeEl.textContent = data.temporary_password;
      alertBox.hidden = false;
    } else {
      alert(`Temporary password for ${username}: ${data.temporary_password}`);
    }
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}
window.resetAdminUserPassword = resetAdminUserPassword;

async function createAdminUser() {
  const uInput = document.getElementById("new-user-username");
  const rInput = document.getElementById("new-user-role");
  const pInput = document.getElementById("new-user-password");
  if (!uInput || !uInput.value.trim()) {
    alert("Please enter a username.");
    return;
  }
  try {
    const res = await fetch("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: uInput.value.trim(),
        role: rInput ? rInput.value : "user",
        password: pInput && pInput.value ? pInput.value : null,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(`Create user failed: ${data.detail || "Could not create user"}`);
      return;
    }
    uInput.value = "";
    if (pInput) pInput.value = "";
    document.getElementById("admin-create-user-card").hidden = true;

    if (data.temporary_password) {
      const alertBox = document.getElementById("admin-temp-pw-alert");
      const codeEl = document.getElementById("admin-temp-pw-display");
      if (alertBox && codeEl) {
        codeEl.textContent = data.temporary_password;
        alertBox.hidden = false;
      }
    }
    loadAdminUsers();
  } catch (e) {
    alert(`Error: ${e.message}`);
  }
}

function openSettingsModal() {
  const dialog = document.getElementById("settings-dialog");
  if (!dialog) return;
  if (!dialog.open) {
    try {
      dialog.showModal();
    } catch (e) {
      console.error("Failed to show settings dialog:", e);
    }
  }
  dialog.scrollTop = 0;
  switchSettingsTab("stab-connections");
  const alertBox = document.getElementById("new-token-alert");
  if (alertBox) alertBox.hidden = true;
  const banner = document.getElementById("import-status-banner");
  if (banner) banner.hidden = true;
  loadSettingsConnections();
  loadSettingsMcp();
  loadAccountInfo();
}

window.openSettingsModal = openSettingsModal;
window.loadAccountInfo = loadAccountInfo;
window.loadAdminUsers = loadAdminUsers;

let currentWizardStep = 1;
let xboxLookupTimer = null;

async function performLiveXboxLink(gamertag, statusContainerId, isWizard = false) {
  const container = document.getElementById(statusContainerId);
  const cleanGt = (gamertag || "").trim();

  if (!cleanGt) {
    if (container) {
      container.hidden = true;
      container.innerHTML = "";
    }
    return false;
  }

  if (container) {
    container.hidden = false;
    container.className = "verification-badge-card loading";
    container.innerHTML = `<span>⏳ Finding Gamertag "${escapeHtml(cleanGt)}"...</span>`;
  }

  try {
    const res = await fetch("/api/user/connections/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platform: "xbox", credential: cleanGt }),
    });
    const data = await res.json();

    if (res.ok && data.status === "ok") {
      const resolvedGt = data.gamertag || cleanGt;

      // 1. Automatically auto-save connection for this user
      try {
        await fetch("/api/user/connections", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ xbox_gamertag: resolvedGt }),
        });
      } catch (saveErr) {
        console.warn("Failed to auto-save Xbox connection:", saveErr);
      }

      // 2. Automatically trigger background sync for Xbox
      try {
        fetch("/api/sync?platform=xbox", { method: "POST" }).catch((e) =>
          console.warn("Auto-sync Xbox trigger warning:", e)
        );
      } catch (syncErr) {
        console.warn("Failed to trigger background sync:", syncErr);
      }

      // 3. Update UI
      if (isWizard) {
        if (container) {
          container.hidden = false;
          container.className = "verification-badge-card success";
          const avatarHtml = data.avatar_url
            ? `<img src="${escapeHtml(data.avatar_url)}" class="verification-avatar" alt="Gamerpic" />`
            : "";
          const scoreHtml =
            data.gamerscore !== undefined && data.gamerscore !== null
              ? `<span class="xbox-player-gamerscore-badge">⭐ ${Number(data.gamerscore).toLocaleString()} G</span>`
              : "";
          container.innerHTML = `
            ${avatarHtml}
            <div class="xbox-player-text">
              <div class="xbox-gamertag-row">
                <span class="xbox-player-gamertag">${escapeHtml(resolvedGt)}</span>
                ${scoreHtml}
              </div>
              <span class="xbox-sync-status">Connected &amp; Auto-Syncing ✓</span>
            </div>
          `;
        }
        const wizInput = document.getElementById("wizard-xbl-key");
        if (wizInput) wizInput.value = resolvedGt;
      } else {
        const inputRow = document.getElementById("setting-xbox-input-row");
        const card = document.getElementById("settings-xbox-connected-card");
        const gtEl = document.getElementById("settings-xbox-gamertag");
        const scoreEl = document.getElementById("settings-xbox-gamerscore");
        const avatarEl = document.getElementById("settings-xbox-avatar");
        const xblInput = document.getElementById("setting-xbl-key");

        if (gtEl) gtEl.textContent = resolvedGt;
        if (scoreEl) {
          if (data.gamerscore !== undefined && data.gamerscore !== null) {
            scoreEl.textContent = "⭐ " + Number(data.gamerscore).toLocaleString() + " G";
            scoreEl.style.display = "";
          } else {
            scoreEl.style.display = "none";
          }
        }
        if (avatarEl) {
          if (data.avatar_url) {
            avatarEl.src = data.avatar_url;
            avatarEl.style.display = "";
          } else {
            avatarEl.style.display = "none";
          }
        }
        if (xblInput) xblInput.value = resolvedGt;
        if (inputRow) inputRow.hidden = true;
        if (card) card.hidden = false;
        if (container) {
          container.hidden = true;
          container.innerHTML = "";
        }
      }
      return true;
    } else {
      if (container) {
        container.hidden = false;
        container.className = "verification-badge-card error";
        const errMsg = data.message || data.detail || "Gamertag not found — check spelling";
        container.innerHTML = `<span>✕ ${escapeHtml(errMsg)}</span>`;
      }
      return false;
    }
  } catch (err) {
    if (container) {
      container.hidden = false;
      container.className = "verification-badge-card error";
      container.innerHTML = `<span>✕ Connection error: ${escapeHtml(err.message)}</span>`;
    }
    return false;
  }
}

async function disconnectXboxAccount() {
  try {
    const res = await fetch("/api/user/connections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ xbox_gamertag: "__CLEAR__", xbl_api_key: "__CLEAR__" }),
    });
    if (res.ok) {
      const inputRow = document.getElementById("setting-xbox-input-row");
      const card = document.getElementById("settings-xbox-connected-card");
      const xblInput = document.getElementById("setting-xbl-key");
      const statusEl = document.getElementById("settings-xbox-status");
      if (card) card.hidden = true;
      if (inputRow) inputRow.hidden = false;
      if (xblInput) {
        xblInput.value = "";
        xblInput.placeholder = "Enter Xbox Gamertag (e.g. Chief117)";
        xblInput.focus();
      }
      if (statusEl) {
        statusEl.hidden = false;
        statusEl.className = "verification-badge-card";
        statusEl.innerHTML = "<span>Xbox disconnected. You can connect a new Gamertag anytime.</span>";
        setTimeout(() => {
          if (statusEl) statusEl.hidden = true;
        }, 3500);
      }
    }
  } catch (err) {
    console.error("Failed to disconnect Xbox account:", err);
  }
}

window.performLiveXboxLink = performLiveXboxLink;
window.disconnectXboxAccount = disconnectXboxAccount;

async function testPlatformConnection(platform, credential, statusContainerId) {
  const container = document.getElementById(statusContainerId);
  if (!container) return false;

  if (!credential || !credential.trim()) {
    container.hidden = false;
    container.className = "verification-badge-card error";
    container.innerHTML = "<span>⚠️ Please enter a credential or account ID before testing.</span>";
    return false;
  }

  container.hidden = false;
  container.className = "verification-badge-card loading";
  container.innerHTML = "<span>⏳ Testing connection to " + platform.toUpperCase() + "...</span>";

  try {
    const res = await fetch("/api/user/connections/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ platform: platform, credential: credential.trim() }),
    });
    const data = await res.json();
    if (res.ok && data.status === "ok") {
      container.className = "verification-badge-card success";
      let html = "<span>✓ " + (data.message || "Verified successfully!") + "</span>";
      if (data.avatar_url) {
        html = '<img src="' + data.avatar_url + '" class="verification-avatar" alt="Avatar" /> ' + html;
      }
      container.innerHTML = html;
      return true;
    } else {
      container.className = "verification-badge-card error";
      container.innerHTML = "<span>✕ " + (data.message || data.detail || "Verification failed") + "</span>";
      return false;
    }
  } catch (err) {
    container.className = "verification-badge-card error";
    container.innerHTML = "<span>✕ Network error testing connection: " + err.message + "</span>";
    return false;
  }
}

function openOnboardingWizard() {
  const dialog = document.getElementById("onboarding-dialog");
  if (!dialog) return;
  if (!dialog.open) {
    try {
      dialog.showModal();
    } catch (e) {
      console.error("Failed to open onboarding dialog:", e);
    }
  }
  dialog.scrollTop = 0;
  switchWizardStep(1);
  loadWizardExistingData();
}

function closeOnboardingWizard() {
  const dialog = document.getElementById("onboarding-dialog");
  if (dialog && dialog.open) {
    dialog.close();
  }
  sessionStorage.setItem("vertex_onboarding_dismissed", "true");
}

async function dismissWizardForever() {
  localStorage.setItem("vertex_onboarding_dismissed", "true");
  sessionStorage.setItem("vertex_onboarding_dismissed", "true");
  try {
    await fetch("/api/user/onboarding/complete", { method: "POST" });
  } catch (e) {
    console.error("Error dismissing onboarding:", e);
  }
  closeOnboardingWizard();
}

function openConnectionsModal() {
  openOnboardingWizard();
}

window.openOnboardingWizard = openOnboardingWizard;
window.closeOnboardingWizard = closeOnboardingWizard;
window.dismissWizardForever = dismissWizardForever;
window.openConnectionsModal = openConnectionsModal;

async function loadWizardExistingData() {
  try {
    const res = await fetch("/api/user/connections");
    if (!res.ok) return;
    const data = await res.json();
    const steamInp = document.getElementById("wizard-steam-id");
    const xblInp = document.getElementById("wizard-xbl-key");
    const psnInp = document.getElementById("wizard-psn-token");
    if (steamInp && data.steam_id) steamInp.value = data.steam_id;
    if (xblInp) {
      if (data.xbox_gamertag) {
        xblInp.value = data.xbox_gamertag;
        performLiveXboxLink(data.xbox_gamertag, "wizard-xbox-status", true);
      } else {
        xblInp.value = "";
        xblInp.placeholder = "Enter Gamertag (e.g. Chief117 or Major Nelson)";
      }
    }
    if (psnInp && data.has_psn_npsso) psnInp.placeholder = data.psn_npsso_masked || "Configured";
  } catch (e) {}
}

function switchWizardStep(stepNum) {
  currentWizardStep = Math.max(1, Math.min(4, stepNum));
  for (let i = 1; i <= 4; i++) {
    const tab = document.getElementById("wstep-tab-" + i);
    const panel = document.getElementById("wstep-panel-" + i);
    if (tab) {
      tab.classList.toggle("active", i === currentWizardStep);
      tab.classList.toggle("completed", i < currentWizardStep);
    }
    if (panel) {
      panel.hidden = i !== currentWizardStep;
    }
  }

  const prevBtn = document.getElementById("btn-wizard-prev");
  const nextBtn = document.getElementById("btn-wizard-next");
  if (prevBtn) {
    prevBtn.disabled = currentWizardStep === 1;
  }
  if (nextBtn) {
    if (currentWizardStep === 4) {
      nextBtn.hidden = true;
    } else {
      nextBtn.hidden = false;
      nextBtn.textContent = "Next →";
    }
  }

  if (currentWizardStep === 4) {
    updateWizardSummary();
  }
}
window.switchWizardStep = switchWizardStep;

async function updateWizardSummary() {
  const list = document.getElementById("wizard-summary-list");
  if (!list) return;
  list.innerHTML = "<span>Checking connections...</span>";

  try {
    const res = await fetch("/api/user/connections");
    const data = res.ok ? await res.json() : {};
    const steamVal = document.getElementById("wizard-steam-id")?.value.trim() || data.steam_id;
    const xblVal = document.getElementById("wizard-xbl-key")?.value.trim() || data.xbox_gamertag;
    const psnVal = document.getElementById("wizard-psn-token")?.value.trim() || data.has_psn_npsso;

    list.innerHTML = "";
    const platforms = [
      { name: "Steam (PC)", active: Boolean(steamVal), icon: BRAND_ICONS.steam },
      { name: "Xbox / PC Game Pass", active: Boolean(xblVal), icon: BRAND_ICONS.xbox },
      { name: "PlayStation (PS4 / PS5)", active: Boolean(psnVal), icon: BRAND_ICONS.playstation },
    ];

    for (const p of platforms) {
      const pill = document.createElement("div");
      pill.className = "connected-summary-pill" + (p.active ? " active" : "");
      pill.innerHTML = p.icon + " " + p.name + ": " + (p.active ? "<strong>Connected ✓</strong>" : "<em>Not connected</em>");
      list.appendChild(pill);
    }
  } catch (err) {
    list.innerHTML = "<span>Unable to load connections summary</span>";
  }
}

async function finishWizardAndSync() {
  const steamInp = document.getElementById("wizard-steam-id");
  const xblInp = document.getElementById("wizard-xbl-key");
  const psnInp = document.getElementById("wizard-psn-token");

  const payload = {};
  if (steamInp && steamInp.value.trim()) payload.steam_id = steamInp.value.trim();
  if (xblInp && xblInp.value.trim()) {
    payload.xbox_gamertag = xblInp.value.trim();
  }
  if (psnInp && psnInp.value.trim()) payload.psn_npsso = psnInp.value.trim();

  try {
    if (Object.keys(payload).length > 0) {
      await fetch("/api/user/connections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    }
    await fetch("/api/user/onboarding/complete", { method: "POST" });
  } catch (e) {
    console.error("Error completing onboarding:", e);
  }

  localStorage.setItem("vertex_onboarding_dismissed", "true");
  closeOnboardingWizard();
  const syncBtn = document.getElementById("btn-sync");
  if (syncBtn) {
    syncBtn.click();
  }
}

async function skipWizard() {
  localStorage.setItem("vertex_onboarding_dismissed", "true");
  try {
    await fetch("/api/user/onboarding/complete", { method: "POST" });
  } catch (e) {}
  closeOnboardingWizard();
}

async function openTriageModal() {
  document.getElementById("library-actions-dialog")?.close();
  const dialog = document.getElementById("triage-dialog");
  const scopeSelect = document.getElementById("triage-scope");
  state.triageScope = scopeSelect.value || "unlocked";

  if (!dialog.open) {
    dialog.showModal();
  }
  dialog.scrollTop = 0;
  await reloadTriageQueue(state.triageScope);
}

async function reloadTriageQueue(scope) {
  state.triageScope = scope;
  let pool = state.games;

  if (scope !== "current_filter") {
    try {
      const res = await fetch("/api/games?sort_by=playtime_desc&limit=2000");
      if (res.ok) {
        const data = await res.json();
        pool = data.games || state.games;
      }
    } catch (err) {
      console.error("Failed to fetch full library for triage:", err);
    }
  }

  let queue = buildTriageQueue(pool, scope);
  if (scope === "unlocked" && queue.length === 0 && pool.length > 0) {
    // Fallback to all games in current view if everything is already locked
    document.getElementById("triage-scope").value = "current_filter";
    state.triageScope = "current_filter";
    queue = [...state.games];
  }

  state.triageQueue = queue;
  state.triageIndex = 0;
  renderTriageCard();
}

function renderTriageCard() {
  const queue = state.triageQueue;
  const idx = state.triageIndex;
  const total = queue.length;

  const counterEl = document.getElementById("triage-counter");
  const progressFill = document.getElementById("triage-progress-fill");
  const cardEl = document.getElementById("triage-card");
  const emptyEl = document.getElementById("triage-empty");
  const prevBtn = document.getElementById("btn-triage-prev");
  const skipBtn = document.getElementById("btn-triage-skip");

  if (total === 0 || idx >= total) {
    counterEl.textContent = `${total} / ${total}`;
    progressFill.style.inlineSize = "100%";
    cardEl.hidden = true;
    emptyEl.hidden = false;
    prevBtn.disabled = total === 0 || idx <= 0;
    skipBtn.disabled = true;
    return;
  }

  cardEl.hidden = false;
  emptyEl.hidden = true;
  prevBtn.disabled = idx <= 0;
  skipBtn.disabled = false;

  counterEl.textContent = `${idx + 1} / ${total}`;
  const pct = Math.round((idx / total) * 100);
  progressFill.style.inlineSize = `${pct}%`;

  const game = queue[idx];
  const fallbackSrc = makeFallbackCover(game.title, game.platform);
  const imgEl = document.getElementById("triage-img");
  const triageWrap = imgEl ? imgEl.closest(".cover-wrapper") : null;
  if (imgEl && triageWrap) {
    attachCoverAspectObserver(triageWrap, imgEl, fallbackSrc, game.cover_url || fallbackSrc);
  }
  imgEl.alt = `${game.title} cover art`;

  // Preload next game's cover art for instant transition
  if (idx + 1 < total && queue[idx + 1].cover_url) {
    const preload = new Image();
    preload.src = queue[idx + 1].cover_url;
  }

  const triagePlatEl = document.getElementById("triage-plat");
  if (triagePlatEl) {
    triagePlatEl.innerHTML = "";
    const triageBrands = createPlatformBrandIconsSpan(game);
    if (triageBrands) triagePlatEl.appendChild(triageBrands);
    const platText = document.createElement("span");
    platText.textContent = game.platform;
    triagePlatEl.appendChild(platText);
  }
  document.getElementById("triage-title").textContent = game.title;

  const metaParts = [game.source];
  if (game.release_year) metaParts.push(`Released ${game.release_year}`);
  if (game.genres && game.genres.length > 0) metaParts.push(game.genres.join(", "));
  if (game.status_locked) metaParts.push("Status Locked");
  document.getElementById("triage-submeta").textContent = metaParts.join(" • ");

  document.getElementById("triage-playtime").textContent = `${game.playtime_hours || 0} hrs`;
  const achTotal = game.achievements_total || 0;
  const achUnl = game.achievements_unlocked || 0;
  document.getElementById("triage-achievements").textContent =
    achTotal > 0 ? `${achUnl} / ${achTotal}` : achUnl > 0 ? `${achUnl} unlocked` : "—";
  document.getElementById("triage-last-played").textContent = formatLastPlayed(game.last_played);

  const curStatus = game.completion_status || "Not Played";
  const statusEl = document.getElementById("triage-current-status");
  statusEl.textContent = curStatus;
  statusEl.dataset.status = curStatus;

  const gpBtn = document.getElementById("btn-triage-gamepass");
  if (gpBtn) {
    const isGp = Boolean(game.is_game_pass);
    gpBtn.innerHTML = `${BRAND_ICONS.gamepass} <span>${isGp ? "On GP" : "Off"}</span>`;
    gpBtn.classList.toggle("active", isGp);
  }

  const ownBtn = document.getElementById("btn-triage-ownership");
  if (ownBtn) {
    const curOwn = game.ownership_status || "Owned";
    ownBtn.textContent = curOwn;
    ownBtn.dataset.ownership = curOwn;
  }

  const favBtn = document.getElementById("btn-triage-fav");
  favBtn.setAttribute("aria-pressed", String(Boolean(game.favorite)));

  for (const btn of document.querySelectorAll(".triage-action-btn")) {
    btn.classList.toggle("active-status", btn.dataset.status === curStatus);
  }

  const ratingContainer = document.getElementById("triage-rating-pills");
  ratingContainer.innerHTML = "";
  for (let r = 1; r <= 10; r++) {
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = `rating-pill ${game.user_rating === r ? "active" : ""}`;
    pill.textContent = String(r);
    pill.title = `Rate ${r}/10`;
    pill.addEventListener("click", async () => {
      const newRating = game.user_rating === r ? null : r;
      game.user_rating = newRating;
      renderTriageCard();
      await quickPatchGame(game.id, { user_rating: newRating });
      showTriageToast(newRating ? `Rated ${game.title}: ${newRating}/10` : "Rating cleared");
    });
    ratingContainer.appendChild(pill);
  }
}

async function applyTriageStatus(newStatus) {
  const queue = state.triageQueue;
  if (state.triageIndex >= queue.length) return;
  const game = queue[state.triageIndex];

  game.completion_status = newStatus;
  game.status_locked = true;
  showTriageToast(`Saved: ${game.title} → ${newStatus}`);

  // Advance immediately (< 5s rapid flow) while persisting in background
  const patchPromise = quickPatchGame(game.id, { completion_status: newStatus });

  runTriageTransition(() => {
    state.triageIndex += 1;
    renderTriageCard();
  }, "forward");

  await patchPromise;
}

async function toggleTriageFavorite() {
  const queue = state.triageQueue;
  if (state.triageIndex >= queue.length) return;
  const game = queue[state.triageIndex];
  const nextFav = !game.favorite;
  game.favorite = nextFav;
  renderTriageCard();
  await quickPatchGame(game.id, { favorite: nextFav });
  showTriageToast(nextFav ? `★ Favorited ${game.title}` : `Removed favorite`);
}

async function toggleTriageGamePass() {
  const queue = state.triageQueue;
  if (state.triageIndex >= queue.length) return;
  const game = queue[state.triageIndex];
  const nextGp = !game.is_game_pass;
  game.is_game_pass = nextGp;
  renderTriageCard();
  let targetId = game.id;
  if (Array.isArray(game.linked_games) && game.linked_games.length > 1) {
    const xboxCopy = game.linked_games.find(
      (lg) => (lg.platform || "").toLowerCase().includes("xbox") || lg.source === "Xbox"
    );
    if (xboxCopy) targetId = xboxCopy.id;
  }
  const updated = await quickPatchGame(targetId, { is_game_pass: nextGp });
  if (updated) {
    queue[state.triageIndex] = updated;
    renderTriageCard();
  }
  showTriageToast(`Game Pass: ${game.title} → ${nextGp ? "On" : "Off"}`);
}

async function toggleTriageOwnership() {
  const queue = state.triageQueue;
  if (state.triageIndex >= queue.length) return;
  const game = queue[state.triageIndex];
  const curOwn = game.ownership_status || "Owned";
  const nextOwn = curOwn === "Owned" ? "Subscription" : "Owned";
  game.ownership_status = nextOwn;
  game.ownership_locked = true;
  renderTriageCard();
  let targetId = game.id;
  if (Array.isArray(game.linked_games) && game.linked_games.length > 1) {
    const xboxCopy = game.linked_games.find(
      (lg) => (lg.platform || "").toLowerCase().includes("xbox") || lg.source === "Xbox"
    );
    if (xboxCopy) targetId = xboxCopy.id;
  }
  const updated = await quickPatchGame(targetId, { ownership_status: nextOwn });
  if (updated) {
    queue[state.triageIndex] = updated;
    renderTriageCard();
  }
  showTriageToast(`Ownership: ${game.title} → ${nextOwn}`);
}

function stepTriage(delta) {
  const nextIdx = state.triageIndex + delta;
  if (nextIdx < 0 || nextIdx > state.triageQueue.length) return;
  const direction = delta >= 0 ? "forward" : "backward";
  runTriageTransition(() => {
    state.triageIndex = nextIdx;
    renderTriageCard();
  }, direction);
}

async function handleCatalogSearch() {
  const input = document.getElementById("catalog-search-input");
  const listEl = document.getElementById("catalog-results");
  const q = input.value.trim();
  if (q.length < 2) return;

  const btn = document.getElementById("btn-catalog-lookup");
  btn.disabled = true;
  btn.textContent = "Searching...";

  try {
    const res = await fetch(`/api/search-external?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    const results = data.results || [];
    listEl.innerHTML = "";

    if (results.length === 0) {
      listEl.hidden = false;
      const li = document.createElement("li");
      li.className = "catalog-item";
      li.textContent = "No catalog matches found — you can fill the fields manually below.";
      listEl.appendChild(li);
      return;
    }

    listEl.hidden = false;
    for (const item of results) {
      const li = document.createElement("li");
      li.className = "catalog-item";

      if (item.cover_url) {
        const img = document.createElement("img");
        img.src = item.cover_url;
        img.alt = "";
        img.width = 36;
        img.height = 48;
        img.loading = "lazy";
        li.appendChild(img);
      }

      const info = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = item.title;
      const meta = document.createElement("div");
      meta.style.fontSize = "0.78rem";
      meta.style.color = "var(--text-secondary)";
      meta.textContent = `${item.release_year ? item.release_year + " • " : ""}${item.provider}`;
      info.appendChild(strong);
      info.appendChild(meta);
      li.appendChild(info);

      li.addEventListener("click", () => {
        document.getElementById("add-title").value = item.title;
        if (item.cover_url) document.getElementById("add-cover").value = item.cover_url;
        if (item.genres && item.genres.length > 0) {
          document.getElementById("add-genres").value = item.genres.join(", ");
        }
        listEl.hidden = true;
      });

      listEl.appendChild(li);
    }
  } catch (err) {
    console.error("Catalog search error:", err);
  } finally {
    btn.disabled = false;
    btn.textContent = "Search Catalog";
  }
}

function syncAddFavButton(isFav) {
  const favCheck = document.getElementById("add-favorite");
  const favBtn = document.getElementById("btn-add-fav");
  if (favCheck) favCheck.checked = Boolean(isFav);
  if (favBtn) {
    favBtn.setAttribute("aria-pressed", String(Boolean(isFav)));
    favBtn.textContent = isFav ? "★ Favorited" : "☆ Favorite";
  }
}

function attachHorizontalSwipe(el, onSwipeLeft, onSwipeRight) {
  if (!el) return;
  let startX = 0;
  let startY = 0;
  let tracking = false;

  el.addEventListener(
    "touchstart",
    (e) => {
      if (e.touches.length !== 1) return;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      tracking = true;
    },
    { passive: true }
  );

  el.addEventListener(
    "touchend",
    (e) => {
      if (!tracking || e.changedTouches.length !== 1) return;
      tracking = false;
      const dx = e.changedTouches[0].clientX - startX;
      const dy = e.changedTouches[0].clientY - startY;
      if (Math.abs(dx) >= 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
        if (dx < 0 && onSwipeLeft) onSwipeLeft();
        if (dx > 0 && onSwipeRight) onSwipeRight();
      }
    },
    { passive: true }
  );
}

function enableDragToScroll(el) {
  if (!el) return;
  let isDown = false;
  let startX = 0;
  let scrollLeft = 0;
  let isDragging = false;

  el.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    isDown = true;
    isDragging = false;
    startX = e.pageX - el.offsetLeft;
    scrollLeft = el.scrollLeft;
  });

  window.addEventListener("mouseup", () => {
    isDown = false;
    if (isDragging) {
      setTimeout(() => {
        isDragging = false;
      }, 50);
    }
  });

  el.addEventListener("mousemove", (e) => {
    if (!isDown) return;
    const x = e.pageX - el.offsetLeft;
    const walk = x - startX;
    if (Math.abs(walk) > 4) {
      isDragging = true;
      el.scrollLeft = scrollLeft - walk;
    }
  });

  el.addEventListener(
    "click",
    (e) => {
      if (isDragging) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    true
  );
}

// ==============================================================================
// Wishlist, Price Monitoring & Smart Upgrades Subsystem
// ==============================================================================

async function fetchWishlistAndDeals() {
  try {
    const statusParam = state.wishlistFilterStatus || "active";
    const [wRes, dRes] = await Promise.all([
      fetch(`/api/wishlist?status=${encodeURIComponent(statusParam)}`),
      fetch("/api/wishlist/deals"),
    ]);

    if (wRes.ok) {
      state.wishlistItems = await wRes.json();
    }
    if (dRes.ok) {
      const dealsData = await dRes.json();
      state.wishlistDeals = dealsData.deals || [];
      updateWishlistKPIs(dealsData);
    }
    renderWishlistGrid();
    checkPushSubscriptionState();
  } catch (err) {
    console.error("Failed to fetch wishlist and deals:", err);
  }
}

function updateWishlistKPIs(dealsData) {
  const deals = (dealsData && dealsData.deals) || [];
  const watchedEl = document.getElementById("wkpi-watched");
  const dealsEl = document.getElementById("wkpi-deals");
  const upgradesEl = document.getElementById("wkpi-upgrades");
  const atlEl = document.getElementById("wkpi-atl");

  const badgeEl = document.getElementById("wishlist-badge");
  const mnavBadgeEl = document.getElementById("mnav-wishlist-badge");

  if (watchedEl) watchedEl.textContent = (state.wishlistItems || []).length;
  if (dealsEl) dealsEl.textContent = deals.length;
  if (upgradesEl) upgradesEl.textContent = deals.filter(d => d.is_smart_upgrade).length;
  if (atlEl) atlEl.textContent = deals.filter(d => d.is_all_time_low).length;

  if (badgeEl) {
    badgeEl.textContent = deals.length;
    badgeEl.hidden = deals.length === 0;
  }
  if (mnavBadgeEl) {
    mnavBadgeEl.textContent = deals.length;
    mnavBadgeEl.hidden = deals.length === 0;
  }
}

function formatWishlistStorefrontLabel(sf) {
  const map = {
    steam: "Steam",
    gog: "GOG",
    epic: "Epic Games",
    fanatical: "Fanatical",
    gmg: "Green Man Gaming",
    greenmangaming: "Green Man Gaming",
    humble: "Humble Store",
    humblestore: "Humble Store",
    gamersgate: "GamersGate",
    xbox_store: "Xbox Store",
    psn_store: "PlayStation Store",
    manual: "Manual Report",
  };
  const key = String(sf || "").toLowerCase().trim();
  return map[key] || (sf ? String(sf).replace(/_/g, " ") : "Storefront");
}

function getWishlistPlatformBrandIconHtml(platformFamily) {
  const fam = String(platformFamily || "").toLowerCase();
  if (fam.includes("xbox")) {
    return `<span class="badge-brand-icon badge-brand-xbox">${BRAND_ICONS.xbox}</span>`;
  }
  if (fam.includes("playstation") || fam.includes("psn") || fam.includes("ps4") || fam.includes("ps5")) {
    return `<span class="badge-brand-icon badge-brand-playstation">${BRAND_ICONS.playstation}</span>`;
  }
  if (fam.includes("switch") || fam.includes("nintendo")) {
    return `<span class="badge-brand-icon badge-brand-switch">${BRAND_ICONS.switch}</span>`;
  }
  return `<span class="badge-brand-icon badge-brand-steam">${BRAND_ICONS.steam}</span>`;
}

function getWishlistStoreBrandIconHtml(storefront, platformFamily) {
  const sf = String(storefront || "").toLowerCase().trim();
  if (sf === "xbox_store") {
    return `<span class="badge-brand-icon badge-brand-xbox">${BRAND_ICONS.xbox}</span>`;
  }
  if (sf === "psn_store") {
    return `<span class="badge-brand-icon badge-brand-playstation">${BRAND_ICONS.playstation}</span>`;
  }
  if (sf === "steam") {
    return `<span class="badge-brand-icon badge-brand-steam">${BRAND_ICONS.steam}</span>`;
  }
  return getWishlistPlatformBrandIconHtml(platformFamily || "PC");
}

function formatWishlistLastScanTimestamp(isoStr) {
  if (!isoStr) return "Not scanned yet";
  try {
    const normalized = isoStr.includes("T") ? (isoStr.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(isoStr) ? isoStr : `${isoStr}Z`) : `${isoStr.replace(" ", "T")}Z`;
    const d = new Date(normalized);
    if (isNaN(d.getTime())) return isoStr;
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch (_e) {
    return isoStr;
  }
}

function renderWishlistGrid() {
  const grid = document.getElementById("wishlist-grid");
  if (!grid) return;

  let items = [...(state.wishlistItems || [])];

  // Filter by platform
  if (state.wishlistFilterPlatform) {
    const fam = state.wishlistFilterPlatform;
    items = items.filter(item => (item.targets || []).some(t => t.platform_family === fam));
  }

  // Filter by Smart Only
  if (state.wishlistFilterSmartOnly) {
    const dealItemIds = new Set(
      state.wishlistDeals.filter(d => d.is_smart_upgrade).map(d => d.wishlist_item_id)
    );
    items = items.filter(item => dealItemIds.has(item.id));
  }

  // Filter by search query
  if (state.wishlistSearch) {
    const q = state.wishlistSearch.toLowerCase().trim();
    items = items.filter(item => {
      const title = item.canonical_game ? item.canonical_game.title.toLowerCase() : "";
      return title.includes(q);
    });
  }

  if (items.length === 0) {
    grid.innerHTML = `
      <div class="empty-state-card" style="grid-column: 1 / -1; padding: 3rem 1.5rem; text-align: center; background: var(--bg-card); border-radius: var(--radius-lg); border: 1px dashed var(--border-subtle);">
        <div style="font-size: 2.5rem; margin-bottom: 0.75rem; color: var(--text-secondary);">${LUCIDE_ICONS.wishlist}</div>
        <h3 style="margin-bottom: 0.5rem;">No tracked games found</h3>
        <p style="color: var(--text-secondary); max-width: 450px; margin: 0 auto 1.25rem;">
          Add games across PC, Xbox, and PlayStation to monitor store prices, detect All-Time Lows, and alert you when Deluxe editions drop to your target price.
        </p>
        <button type="button" class="btn btn-primary" onclick="openWishlistAddModal()">+ Watch Your First Game</button>
      </div>
    `;
    return;
  }

  const dealsByItemId = {};
  for (const deal of state.wishlistDeals || []) {
    if (!dealsByItemId[deal.wishlist_item_id]) dealsByItemId[deal.wishlist_item_id] = [];
    dealsByItemId[deal.wishlist_item_id].push(deal);
  }

  grid.innerHTML = items.map(item => {
    const canonical = item.canonical_game || {};
    const itemDeals = dealsByItemId[item.id] || [];
    const smartUpgradeDeal = itemDeals.find(d => d.is_smart_upgrade);
    const hasDeal = itemDeals.length > 0;
    const coverUrl = canonical.cover_url || "/static/vertex_logo.svg";

    const editionsById = {};
    for (const ed of item.editions || []) {
      editionsById[ed.id] = ed;
    }

    const skusByFamilyMap = {};
    for (const sku of item.skus || []) {
      const fam = sku.platform_family || "PC";
      if (!skusByFamilyMap[fam]) skusByFamilyMap[fam] = new Map();
      const dedupeKey = `${String(sku.storefront || "").toLowerCase()}:${sku.edition_id || 0}`;
      const existing = skusByFamilyMap[fam].get(dedupeKey);
      if (!existing) {
        skusByFamilyMap[fam].set(dedupeKey, sku);
      } else {
        const curIsDirect = sku.store_url && !sku.store_url.includes("cheapshark.com/redirect");
        const exIsDirect = existing.store_url && !existing.store_url.includes("cheapshark.com/redirect");
        if (curIsDirect && !exIsDirect) {
          skusByFamilyMap[fam].set(dedupeKey, sku);
        }
      }
    }
    const skusByFamily = {};
    for (const [fam, mapObj] of Object.entries(skusByFamilyMap)) {
      skusByFamily[fam] = Array.from(mapObj.values());
    }

    const allTargets = item.targets || [];
    const monitoredTargets = allTargets.filter(t => t.is_active || t.muted_owned_conflict);
    const displayTargets = monitoredTargets.length > 0 ? monitoredTargets : allTargets;
    if (!state.expandedWishlistPlatforms) state.expandedWishlistPlatforms = new Set();

    const targetsHtml = displayTargets.map(target => {
      const fam = target.platform_family;
      const expandKey = `${item.id}:${fam}`;
      const isExpanded = state.expandedWishlistPlatforms.has(expandKey);
      const targetPriceNum = Number(target.target_price_usd || 0);
      const priceText = `$${targetPriceNum.toFixed(2)}`;
      const famIcon = getWishlistPlatformBrandIconHtml(fam);
      const familySkus = (skusByFamily[fam] || []).slice().sort((a, b) => {
        const pa = a.current_price_usd !== null && a.current_price_usd !== undefined ? Number(a.current_price_usd) : 999999;
        const pb = b.current_price_usd !== null && b.current_price_usd !== undefined ? Number(b.current_price_usd) : 999999;
        return pa - pb;
      });

      const pricedSkus = familySkus.filter(s => s.current_price_usd !== null && s.current_price_usd !== undefined);
      const bestSku = pricedSkus.length > 0 ? pricedSkus[0] : null;
      const bestPriceNum = bestSku ? Number(bestSku.current_price_usd) : null;
      const bestStoreLabel = bestSku ? formatWishlistStorefrontLabel(bestSku.storefront) : "";

      const targetDeal = itemDeals.find(d => d.platform_family === fam);
      const isMet = Boolean(targetDeal) || (
        bestPriceNum !== null && !target.muted_owned_conflict && target.is_active && bestPriceNum <= targetPriceNum
      );

      let groupClass = "wishlist-platform-group";
      if (!target.is_active || target.muted_owned_conflict) {
        groupClass += " muted";
      } else if (isMet) {
        groupClass += " met";
      }

      const storeCountLabel = familySkus.length > 0
        ? `<span class="abridged-store-hint">${bestStoreLabel ? `${escapeHtml(bestStoreLabel)}${familySkus.length > 1 ? ` +${familySkus.length - 1}` : ""}` : `${familySkus.length} stores`}</span>`
        : `<span class="abridged-store-hint">Pending scan</span>`;

      let abridgedRightHtml = "";
      if (target.muted_owned_conflict) {
        abridgedRightHtml = `<span class="text-secondary" style="font-size: 0.75rem;">Muted (Owned) &bull; Target ${priceText}</span>`;
      } else if (!target.is_active) {
        abridgedRightHtml = `<span class="text-secondary" style="font-size: 0.75rem;">Paused &bull; Target ${priceText}</span>`;
      } else if (bestPriceNum !== null) {
        abridgedRightHtml = `
          <span class="abridged-lowest-price ${isMet ? 'met' : ''}">$${bestPriceNum.toFixed(2)}</span>
          <span class="abridged-target-sub">(Target ${priceText})${isMet ? ' (Met)' : ''}</span>
        `;
      } else {
        abridgedRightHtml = `
          <span class="abridged-lowest-price unpriced">—</span>
          <span class="abridged-target-sub">(Target ${priceText})</span>
        `;
      }

      let marketplaceRowsHtml = "";
      if (familySkus.length === 0) {
        marketplaceRowsHtml = `
          <div class="marketplace-price-row empty">
            <div class="marketplace-meta">
              <span class="marketplace-name">No store listing found yet</span>
              <span class="marketplace-scan-time">Last scan: Pending sync</span>
            </div>
            <div class="marketplace-price-compare">
              <span class="marketplace-current-price unpriced">—</span>
              <span class="marketplace-vs-sep">→</span>
              <span class="marketplace-target-price">Target ${priceText}</span>
            </div>
          </div>
        `;
      } else {
        marketplaceRowsHtml = familySkus.map(sku => {
          const ed = editionsById[sku.edition_id] || {};
          const isUpgradeEd = ed.edition_tier && ed.edition_tier !== "base";
          const sfLabel = formatWishlistStorefrontLabel(sku.storefront);
          const sfBrandIcon = getWishlistStoreBrandIconHtml(sku.storefront, fam);
          const hasPrice = sku.current_price_usd !== null && sku.current_price_usd !== undefined;
          const curPriceNum = hasPrice ? Number(sku.current_price_usd) : null;
          const skuMet = hasPrice && !target.muted_owned_conflict && target.is_active && curPriceNum <= targetPriceNum;
          const scanTimeStr = formatWishlistLastScanTimestamp(sku.last_checked_at);
          const discountBadge = sku.discount_pct && Number(sku.discount_pct) > 0
            ? `<span class="marketplace-discount-badge">-${Number(sku.discount_pct)}%</span>`
            : "";
          const editionBadge = isUpgradeEd && ed.edition_name
            ? `<span class="marketplace-edition-tag">${escapeHtml(ed.edition_name)}</span>`
            : `<span class="marketplace-edition-tag marketplace-edition-base">Standard</span>`;
          const storeNameNode = sku.store_url
            ? `<a href="${escapeHtml(sku.store_url)}" target="_blank" rel="noopener noreferrer" class="marketplace-name-link" title="Open ${escapeHtml(sfLabel)} store listing">${sfBrandIcon}<span>${escapeHtml(sfLabel)}</span></a>`
            : `<span class="marketplace-name">${sfBrandIcon}<span>${escapeHtml(sfLabel)}</span></span>`;
          const storeVisitBtn = sku.store_url
            ? `<a href="${escapeHtml(sku.store_url)}" target="_blank" rel="noopener noreferrer" class="btn-store-link" title="1-click open ${escapeHtml(sfLabel)} listing" aria-label="Open ${escapeHtml(sfLabel)} store page">Store ↗</a>`
            : "";

          return `
            <div class="marketplace-price-row ${skuMet ? 'deal-met' : ''}">
              <div class="marketplace-meta">
                <div class="marketplace-title-line">
                  ${storeNameNode}
                  ${editionBadge}
                  ${discountBadge}
                </div>
                <div class="marketplace-scan-time" title="${escapeHtml(sku.last_checked_at || '')}">
                  Last scan: ${escapeHtml(scanTimeStr)}
                </div>
              </div>
              <div class="marketplace-price-compare">
                <span class="marketplace-current-price ${skuMet ? 'met' : ''}">
                  ${hasPrice ? `$${curPriceNum.toFixed(2)}` : '—'}
                </span>
                <span class="marketplace-vs-sep">/</span>
                <span class="marketplace-target-price" title="Your ${escapeHtml(fam)} target price">
                  Target ${priceText}
                </span>
                ${storeVisitBtn}
              </div>
            </div>
          `;
        }).join("");
      }

      return `
        <details class="${groupClass}" ${isExpanded ? "open" : ""} ontoggle="window.onToggleWishlistPlatformDetails && window.onToggleWishlistPlatformDetails(${item.id}, '${escapeHtml(fam)}', this.open)">
          <summary class="wishlist-platform-header wishlist-abridged-row" title="Click to toggle all ${familySkus.length} store prices for ${escapeHtml(fam)}">
            <div class="abridged-platform-left">
              <span class="abridged-platform-brand">${famIcon} <strong>${escapeHtml(fam === "PC" ? "PC (Steam)" : fam)}</strong></span>
              ${storeCountLabel}
            </div>
            <div class="abridged-platform-right">
              ${abridgedRightHtml}
              <span class="abridged-expand-caret" aria-hidden="true">▾</span>
            </div>
          </summary>
          <div class="marketplace-rows-list">
            ${marketplaceRowsHtml}
          </div>
        </details>
      `;
    }).join("");

    const cardPlatformIconsHtml = displayTargets.map(t => getWishlistPlatformBrandIconHtml(t.platform_family)).join("");
    const cardPlatformLabels = displayTargets.map(t => t.platform_family === "PC" ? "PC (Steam)" : t.platform_family).join(" + ");

    return `
      <article class="wishlist-card ${hasDeal ? 'has-deal' : ''} ${smartUpgradeDeal ? 'has-smart-upgrade' : ''}">
        <div class="wishlist-card-media">
          <img src="${escapeHtml(coverUrl)}" alt="${escapeHtml(canonical.title || '')}" class="wishlist-card-cover" loading="lazy" onerror="if(!this.dataset.fallback){this.dataset.fallback='1';this.src='/static/vertex_logo.svg';}" />
          <div class="platform-badge wishlist-cover-platform-badge">
            <span class="platform-brand-icons">${cardPlatformIconsHtml}</span>
            <span>${escapeHtml(cardPlatformLabels)}</span>
          </div>
          <div class="wishlist-badges-strip">
            ${smartUpgradeDeal ? `<div class="smart-upgrade-callout">Smart Upgrade: ${escapeHtml(smartUpgradeDeal.edition_name)} $${smartUpgradeDeal.current_price_usd.toFixed(2)}</div>` : ''}
            ${itemDeals.some(d => d.is_all_time_low) ? `<div class="atl-badge">All-Time Low</div>` : ''}
          </div>
        </div>
        <div class="wishlist-card-body">
          <h3 class="wishlist-card-title">${escapeHtml(canonical.title || 'Untitled Game')}</h3>
          <div style="font-size: 0.8rem; color: var(--text-secondary);">
            ${canonical.release_year ? `<span>${canonical.release_year}</span> &bull; ` : ''}
            <span>${escapeHtml(canonical.developer || canonical.publisher || 'Cross-Platform')}</span>
          </div>

          <div class="wishlist-targets-list">
            ${targetsHtml}
          </div>

          ${item.notes ? `<div style="font-size: 0.8rem; color: var(--text-secondary); font-style: italic;">${escapeHtml(item.notes)}</div>` : ''}
        </div>
        <div class="wishlist-card-footer">
          <div style="display: flex; gap: 0.35rem;">
            <button type="button" class="btn btn-sm btn-secondary" onclick="openWishlistHistoryModal(${item.id})">History</button>
            <button type="button" class="btn btn-sm btn-secondary" title="Edit monitored platforms &amp; target prices" onclick="openWishlistEditPlatformsModal(${item.id})">Stores</button>
          </div>
          <div style="display: flex; gap: 0.4rem;">
            ${item.status === 'active' ? `<button type="button" class="btn btn-sm btn-primary" onclick="openWishlistPurchaseModal(${item.id})">Mark Purchased</button>` : `<span class="badge-pill" style="background: rgba(16,185,129,0.2); color: #34d399;">Purchased</span>`}
            <button type="button" class="btn btn-sm btn-icon" title="Remove from wishlist" onclick="deleteWishlistItem(${item.id})">${LUCIDE_ICONS.trash}</button>
          </div>
        </div>
      </article>
    `;
  }).join("");
}

function openWishlistAddModal() {
  const dialog = document.getElementById("wishlist-add-dialog");
  if (!dialog) return;
  state.selectedCandidate = null;
  state.editingWishlistItemId = null;
  const titleEl = document.getElementById("wishlist-add-title");
  if (titleEl) titleEl.textContent = "Watch Game on Wishlist";
  const backBtn = document.getElementById("btn-back-to-candidates");
  if (backBtn) backBtn.hidden = false;
  const stepSearch = document.getElementById("wishlist-step-search");
  const stepConfig = document.getElementById("wishlist-config-form");
  const candidateInput = document.getElementById("wishlist-candidate-input");
  const candidatesList = document.getElementById("candidates-list");

  if (stepSearch) stepSearch.hidden = false;
  if (stepConfig) stepConfig.hidden = true;
  if (candidatesList) candidatesList.innerHTML = "";
  if (candidateInput) {
    candidateInput.value = "";
    setTimeout(() => candidateInput.focus(), 100);
  }
  dialog.showModal();
}

function openWishlistEditPlatformsModal(itemId) {
  const item = (state.wishlistItems || []).find(i => i.id === itemId);
  if (!item) return;
  const dialog = document.getElementById("wishlist-add-dialog");
  if (!dialog) return;

  const canonical = item.canonical_game || {};
  state.editingWishlistItemId = item.id;
  state.selectedCandidate = {
    title: canonical.title || "Untitled Game",
    release_year: canonical.release_year,
    developer: canonical.developer || "",
    publisher: canonical.publisher || "",
    cover_url: canonical.cover_url,
    genres: canonical.genres || [],
    critic_score: canonical.critic_score,
    ownership_conflict: item.conflict || {},
  };

  const titleEl = document.getElementById("wishlist-add-title");
  if (titleEl) titleEl.textContent = "Edit Monitored Stores & Target Prices";

  const stepSearch = document.getElementById("wishlist-step-search");
  const stepConfig = document.getElementById("wishlist-config-form");
  const backBtn = document.getElementById("btn-back-to-candidates");
  const preview = document.getElementById("selected-game-preview");

  if (stepSearch) stepSearch.hidden = true;
  if (stepConfig) stepConfig.hidden = false;
  if (backBtn) backBtn.hidden = true;

  if (preview) {
    preview.innerHTML = `
      <div style="display: flex; gap: 1rem; align-items: center; margin-bottom: 1.25rem; padding: 0.75rem; background: rgba(255,255,255,0.03); border-radius: var(--radius-md);">
        <img src="${escapeHtml(canonical.cover_url || '/static/vertex_logo.svg')}" style="width: 48px; height: 72px; object-fit: cover; border-radius: 4px;" alt="" />
        <div>
          <h3 style="margin: 0; font-size: 1.1rem;">${escapeHtml(canonical.title || '')}</h3>
          <div style="font-size: 0.8rem; color: var(--text-secondary);">${canonical.release_year || ''} &bull; ${escapeHtml(canonical.developer || canonical.publisher || 'Cross-Platform')}</div>
        </div>
      </div>
    `;
  }

  const targetsByFam = {};
  for (const t of item.targets || []) {
    targetsByFam[t.platform_family] = t;
  }

  const conflict = item.conflict || {};
  const conflictedPlats = new Set(conflict.conflicted_platforms || []);

  const pcTarget = targetsByFam["PC"];
  const pcCheck = document.getElementById("cfg-pc-active");
  const pcPrice = document.getElementById("cfg-pc-price");
  const pcBadge = document.getElementById("pc-conflict-badge");
  if (pcCheck) pcCheck.checked = pcTarget ? Boolean(pcTarget.is_active) : false;
  if (pcPrice && pcTarget) pcPrice.value = Number(pcTarget.target_price_usd).toFixed(2);
  if (pcBadge) {
    const isOwned = conflictedPlats.has("PC");
    pcBadge.hidden = !isOwned;
    pcBadge.textContent = isOwned ? "Owned in Library" : "";
  }

  const xbTarget = targetsByFam["Xbox"];
  const xbCheck = document.getElementById("cfg-xbox-active");
  const xbPrice = document.getElementById("cfg-xbox-price");
  const xbBadge = document.getElementById("xbox-conflict-badge");
  if (xbCheck) xbCheck.checked = xbTarget ? Boolean(xbTarget.is_active) : false;
  if (xbPrice && xbTarget) xbPrice.value = Number(xbTarget.target_price_usd).toFixed(2);
  if (xbBadge) {
    const isOwned = conflictedPlats.has("Xbox");
    xbBadge.hidden = !isOwned;
    xbBadge.textContent = isOwned ? "Owned in Library" : "";
  }

  const psTarget = targetsByFam["PlayStation"];
  const psCheck = document.getElementById("cfg-psn-active");
  const psPrice = document.getElementById("cfg-psn-price");
  const psBadge = document.getElementById("psn-conflict-badge");
  if (psCheck) psCheck.checked = psTarget ? Boolean(psTarget.is_active) : false;
  if (psPrice && psTarget) psPrice.value = Number(psTarget.target_price_usd).toFixed(2);
  if (psBadge) {
    const isOwned = conflictedPlats.has("PlayStation");
    psBadge.hidden = !isOwned;
    psBadge.textContent = isOwned ? "Owned in Library" : "";
  }

  const smartCheck = document.getElementById("cfg-smart-upgrade");
  if (smartCheck) smartCheck.checked = Boolean(item.allow_smart_upgrade);

  const notesInput = document.getElementById("cfg-wishlist-notes");
  if (notesInput) notesInput.value = item.notes || "";

  dialog.showModal();
}

async function searchWishlistCandidates() {
  const input = document.getElementById("wishlist-candidate-input");
  const loading = document.getElementById("candidates-loading");
  const list = document.getElementById("candidates-list");
  if (!input || !list) return;

  const q = input.value.trim();
  if (!q) return;

  if (loading) loading.hidden = false;
  list.innerHTML = "";

  try {
    const res = await fetch(`/api/wishlist/candidates?q=${encodeURIComponent(q)}`);
    if (!res.ok) throw new Error("Search failed");
    const data = await res.json();
    const candidates = data.candidates || [];

    if (loading) loading.hidden = true;

    if (candidates.length === 0) {
      list.innerHTML = `<p style="padding: 1rem; color: var(--text-secondary); text-align: center;">No candidate titles found matching "${escapeHtml(q)}". Try a simpler title.</p>`;
      return;
    }

    list.innerHTML = candidates.map((c, idx) => {
      const conflict = c.ownership_conflict || {};
      const hasConflict = conflict.already_in_library;
      const cover = c.cover_url || "/static/vertex_logo.svg";

      return `
        <div class="candidate-card ${hasConflict ? 'has-conflict' : ''}">
          <img src="${escapeHtml(cover)}" alt="" class="candidate-cover" />
          <div class="candidate-info">
            <div class="candidate-title">${escapeHtml(c.title)}</div>
            <div class="candidate-meta">
              ${c.release_year ? `<span>${c.release_year}</span> &bull; ` : ''}
              <span>${escapeHtml(c.developer || c.publisher || 'Video Game')}</span>
            </div>
            ${hasConflict ? `<div class="conflict-warning-badge">⚠️ ${escapeHtml(conflict.conflict_warning || 'Already in Library')}</div>` : ''}
          </div>
          <button type="button" class="btn btn-sm btn-primary" onclick="selectCandidateForConfig(${idx})">Select &amp; Configure</button>
        </div>
      `;
    }).join("");

    state.currentSearchCandidates = candidates;
  } catch (err) {
    if (loading) loading.hidden = true;
    list.innerHTML = `<p style="padding: 1rem; color: #f87171; text-align: center;">Failed to search candidates: ${escapeHtml(err.message)}</p>`;
  }
}

function selectCandidateForConfig(idx) {
  const candidates = state.currentSearchCandidates || [];
  const candidate = candidates[idx];
  if (!candidate) return;
  state.selectedCandidate = candidate;

  const stepSearch = document.getElementById("wishlist-step-search");
  const stepConfig = document.getElementById("wishlist-config-form");
  const preview = document.getElementById("selected-game-preview");

  if (stepSearch) stepSearch.hidden = true;
  if (stepConfig) stepConfig.hidden = false;

  const conflict = candidate.ownership_conflict || {};
  const conflictedPlats = new Set(conflict.conflicted_platforms || []);

  if (preview) {
    const ownedBanner = conflict.already_in_library
      ? `<div style="margin-top: 0.4rem; font-size: 0.78rem; color: #fbbf24; background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.3); padding: 0.3rem 0.6rem; border-radius: 6px;">
           ℹ️ ${escapeHtml(conflict.conflict_warning || 'Detected in library')}. Unchecked platforms below will be hidden unless you check them to monitor anyway.
         </div>`
      : "";
    preview.innerHTML = `
      <div style="display: flex; gap: 1rem; align-items: center; margin-bottom: 1.25rem; padding: 0.75rem; background: rgba(255,255,255,0.03); border-radius: var(--radius-md);">
        <img src="${escapeHtml(candidate.cover_url || '/static/vertex_logo.svg')}" style="width: 48px; height: 72px; object-fit: cover; border-radius: 4px;" alt="" />
        <div style="flex: 1;">
          <h3 style="margin: 0; font-size: 1.1rem;">${escapeHtml(candidate.title)}</h3>
          <div style="font-size: 0.8rem; color: var(--text-secondary);">${candidate.release_year || ''} &bull; ${escapeHtml(candidate.developer || candidate.publisher || '')}</div>
          ${ownedBanner}
        </div>
      </div>
    `;
  }

  // Set default targets and conflict auto-muting
  const pcCheck = document.getElementById("cfg-pc-active");
  const pcBadge = document.getElementById("pc-conflict-badge");
  if (pcCheck) {
    const isOwned = conflictedPlats.has("PC");
    pcCheck.checked = !isOwned;
    if (pcBadge) {
      pcBadge.hidden = !isOwned;
      pcBadge.textContent = isOwned ? "Auto-unchecked (Owned in Library — check to monitor anyway)" : "";
    }
  }

  const xbCheck = document.getElementById("cfg-xbox-active");
  const xbBadge = document.getElementById("xbox-conflict-badge");
  if (xbCheck) {
    const isOwned = conflictedPlats.has("Xbox");
    xbCheck.checked = !isOwned;
    if (xbBadge) {
      xbBadge.hidden = !isOwned;
      xbBadge.textContent = isOwned ? "Auto-unchecked (Owned in Library — check to monitor anyway)" : "";
    }
  }

  const psCheck = document.getElementById("cfg-psn-active");
  const psBadge = document.getElementById("psn-conflict-badge");
  if (psCheck) {
    const isOwned = conflictedPlats.has("PlayStation");
    psCheck.checked = !isOwned;
    if (psBadge) {
      psBadge.hidden = !isOwned;
      psBadge.textContent = isOwned ? "Auto-unchecked (Owned in Library — check to monitor anyway)" : "";
    }
  }
}

async function submitWishlistConfig(e) {
  if (e) e.preventDefault();
  const c = state.selectedCandidate;
  if (!c) return;

  const targets = [];
  const pcActive = Boolean(document.getElementById("cfg-pc-active")?.checked);
  const pcPrice = parseFloat(document.getElementById("cfg-pc-price")?.value) || 19.99;
  targets.push({ platform: "PC", target_price_usd: pcPrice, is_active: pcActive });

  const xbActive = Boolean(document.getElementById("cfg-xbox-active")?.checked);
  const xbPrice = parseFloat(document.getElementById("cfg-xbox-price")?.value) || 29.99;
  targets.push({ platform: "Xbox", target_price_usd: xbPrice, is_active: xbActive });

  const psActive = Boolean(document.getElementById("cfg-psn-active")?.checked);
  const psPrice = parseFloat(document.getElementById("cfg-psn-price")?.value) || 29.99;
  targets.push({ platform: "PlayStation", target_price_usd: psPrice, is_active: psActive });

  const allowSmart = document.getElementById("cfg-smart-upgrade")?.checked ?? true;
  const notes = document.getElementById("cfg-wishlist-notes")?.value || "";

  const conflict = c.ownership_conflict || {};
  const conflictedPlats = new Set(conflict.conflicted_platforms || []);
  const userOverrodeOwned = (pcActive && conflictedPlats.has("PC")) ||
                           (xbActive && conflictedPlats.has("Xbox")) ||
                           (psActive && conflictedPlats.has("PlayStation"));

  const payload = {
    title: c.title,
    release_year: c.release_year,
    developer: c.developer || "",
    publisher: c.publisher || "",
    cover_url: c.cover_url,
    genres: c.genres || [],
    critic_score: c.critic_score,
    platform_targets: targets,
    allow_smart_upgrade: allowSmart,
    notes: notes,
    force_track_owned: userOverrodeOwned,
  };

  try {
    const saveBtn = document.getElementById("btn-save-wishlist-item");
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "⏳ Checking Stores...";
    }
    if (state.editingWishlistItemId) {
      const editId = state.editingWishlistItemId;
      const res = await fetch(`/api/wishlist/${editId}/platforms`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform_targets: targets,
          allow_smart_upgrade: allowSmart,
          force_track_owned: userOverrodeOwned,
        }),
      });
      if (!res.ok) throw new Error("Failed to update monitored platforms");
      if (notes !== undefined) {
        await fetch(`/api/wishlist/${editId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ notes }),
        });
      }
      state.editingWishlistItemId = null;
    } else {
      const res = await fetch("/api/wishlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error("Failed to save wishlist item");
    }
    document.getElementById("wishlist-add-dialog")?.close();
    fetchWishlistAndDeals();
  } catch (err) {
    alert(`Could not save wishlist item: ${err.message}`);
  } finally {
    const saveBtn = document.getElementById("btn-save-wishlist-item");
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save to Wishlist";
    }
  }
}

async function deleteWishlistItem(itemId) {
  if (!confirm("Remove this game from your wishlist?")) return;
  try {
    const res = await fetch(`/api/wishlist/${itemId}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to delete item");
    fetchWishlistAndDeals();
  } catch (err) {
    alert(`Error removing item: ${err.message}`);
  }
}

async function openWishlistHistoryModal(itemId) {
  const dialog = document.getElementById("wishlist-history-dialog");
  const body = document.getElementById("wishlist-history-body");
  if (!dialog || !body) return;

  body.innerHTML = `<p style="text-align: center; color: var(--text-secondary);">Loading price history...</p>`;
  dialog.showModal();

  try {
    const res = await fetch(`/api/wishlist/${itemId}/history`);
    if (!res.ok) throw new Error("Failed to load history");
    const data = await res.json();
    const item = data.item || {};
    const historyBySku = data.price_history_by_sku || {};
    const skus = item.skus || [];

    let contentHtml = `
      <div style="display: flex; gap: 1rem; align-items: center; margin-bottom: 1.25rem;">
        <img src="${escapeHtml(item.canonical_game?.cover_url || '/static/vertex_logo.svg')}" style="width: 50px; height: 75px; object-fit: cover; border-radius: 4px;" alt="" />
        <div>
          <h3 style="margin: 0; font-size: 1.15rem;">${escapeHtml(item.canonical_game?.title || '')}</h3>
          <div style="font-size: 0.85rem; color: var(--text-secondary);">Tracked across ${skus.length} storefront SKUs</div>
        </div>
      </div>
    `;

    if (skus.length === 0) {
      contentHtml += `<p style="color: var(--text-secondary); text-align: center;">No storefront SKUs recorded yet. Click "Sync Prices Now" on the dashboard to check stores.</p>`;
    } else {
      const targetsByFamily = {};
      for (const t of item.targets || []) {
        targetsByFamily[t.platform_family] = t;
      }
      const editionsById = {};
      for (const ed of item.editions || []) {
        editionsById[ed.id] = ed;
      }

      contentHtml += skus.map(s => {
        const history = historyBySku[s.id] || [];
        const target = targetsByFamily[s.platform_family];
        const targetPriceStr = target && target.target_price_usd !== undefined
          ? `$${Number(target.target_price_usd).toFixed(2)}`
          : null;
        const sfLabel = formatWishlistStorefrontLabel(s.storefront);
        const sfBrandIcon = getWishlistStoreBrandIconHtml(s.storefront, s.platform_family);
        const scanStr = formatWishlistLastScanTimestamp(s.last_checked_at);
        const ed = editionsById[s.edition_id] || {};
        const edName = ed.edition_name || "Standard Edition";
        const storeBtn = s.store_url
          ? `<a href="${escapeHtml(s.store_url)}" target="_blank" rel="noopener noreferrer" class="btn-store-link" style="margin-left: 0.4rem;">Store ↗</a>`
          : "";
        return `
          <div style="margin-bottom: 1.25rem; background: rgba(255,255,255,0.03); padding: 0.85rem; border-radius: var(--radius-md); border: 1px solid var(--border-subtle);">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 0.75rem; margin-bottom: 0.5rem; flex-wrap: wrap;">
              <div>
                <strong style="display: inline-flex; align-items: center; gap: 0.35rem;">${sfBrandIcon}<span>${escapeHtml(sfLabel)} &bull; ${escapeHtml(edName)}</span></strong>
                ${storeBtn}
                <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.15rem;">
                  ${escapeHtml(s.platform_label || s.platform_family)} &bull; Last scan: ${escapeHtml(scanStr)}
                </div>
              </div>
              <div style="text-align: right;">
                <span>Current: <strong>${s.current_price_usd !== null && s.current_price_usd !== undefined ? `$${Number(s.current_price_usd).toFixed(2)}` : '—'}</strong></span>
                ${targetPriceStr ? `<span style="font-size: 0.82rem; color: var(--text-secondary); margin-left: 0.45rem;">/ Target: <strong>${targetPriceStr}</strong></span>` : ''}
                ${s.historical_low_usd ? `<span style="font-size: 0.78rem; color: #a78bfa; margin-left: 0.5rem;">ATL: $${Number(s.historical_low_usd).toFixed(2)}</span>` : ''}
              </div>
            </div>
            <div style="max-height: 140px; overflow-y: auto; font-size: 0.82rem;">
              ${history.length === 0 ? '<div style="color: var(--text-secondary);">No history entries yet.</div>' : `
                <table style="width: 100%; border-collapse: collapse;">
                  <thead>
                    <tr style="color: var(--text-secondary); text-align: left; border-bottom: 1px solid var(--border-subtle);">
                      <th style="padding: 0.25rem;">Date</th>
                      <th style="padding: 0.25rem;">Price</th>
                      <th style="padding: 0.25rem;">Discount</th>
                      <th style="padding: 0.25rem;">Flag</th>
                    </tr>
                  </thead>
                  <tbody>
                    ${history.map(h => `
                      <tr style="border-bottom: 1px solid rgba(255,255,255,0.02);">
                        <td style="padding: 0.25rem;">${h.recorded_at ? h.recorded_at.slice(0, 10) : ''}</td>
                        <td style="padding: 0.25rem; font-weight: 600;">$${h.price_usd.toFixed(2)}</td>
                        <td style="padding: 0.25rem;">${h.discount_pct ? `${h.discount_pct}%` : '—'}</td>
                        <td style="padding: 0.25rem;">${h.is_all_time_low ? '<span style="color: #a78bfa; font-weight: 700;">ATL</span>' : ''}</td>
                      </tr>
                    `).join('')}
                  </tbody>
                </table>
              `}
            </div>
          </div>
        `;
      }).join("");
    }

    body.innerHTML = contentHtml;
  } catch (err) {
    body.innerHTML = `<p style="color: #f87171; text-align: center;">Error loading price history: ${escapeHtml(err.message)}</p>`;
  }
}

function openWishlistPurchaseModal(itemId) {
  const dialog = document.getElementById("wishlist-purchase-dialog");
  const idInput = document.getElementById("purchase-item-id");
  if (!dialog || !idInput) return;
  idInput.value = itemId;

  const item = (state.wishlistItems || []).find(i => i.id === itemId);
  if (item && item.canonical_game) {
    const titleEl = document.getElementById("purchase-dialog-title");
    if (titleEl) titleEl.textContent = `Mark as Purchased: ${item.canonical_game.title}`;
  }
  dialog.showModal();
}

async function submitWishlistPurchase(e) {
  if (e) e.preventDefault();
  const itemId = parseInt(document.getElementById("purchase-item-id")?.value, 10);
  if (!itemId) return;

  const platform = document.getElementById("purchase-platform")?.value || "PC";
  const editionName = document.getElementById("purchase-edition")?.value || "Standard Edition";
  const pricePaid = parseFloat(document.getElementById("purchase-price")?.value) || null;
  const compStatus = document.getElementById("purchase-status")?.value || "Plan to Play";

  try {
    const res = await fetch(`/api/wishlist/${itemId}/purchase`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        platform: platform,
        edition_name: editionName,
        price_paid_usd: pricePaid,
        completion_status: compStatus,
      }),
    });
    if (!res.ok) throw new Error("Failed to record purchase");
    document.getElementById("wishlist-purchase-dialog")?.close();
    fetchWishlistAndDeals();
    fetchStats();
  } catch (err) {
    alert(`Could not mark purchase: ${err.message}`);
  }
}

async function syncWishlistPrices() {
  const btn = document.getElementById("btn-sync-wishlist");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Syncing...";
  }
  try {
    const res = await fetch("/api/wishlist/sync", { method: "POST" });
    if (!res.ok) throw new Error("Sync failed");
    await res.json();
    fetchWishlistAndDeals();
  } catch (err) {
    alert(`Price sync error: ${err.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Sync Prices";
    }
  }
}

function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/\-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}

async function checkPushSubscriptionState() {
  const btn = document.getElementById("btn-toggle-push");
  const icon = document.getElementById("push-toggle-icon");
  const label = document.getElementById("push-toggle-label");
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    if (btn) btn.hidden = true;
    return;
  }

  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    state.pushSubscribed = Boolean(sub);
    if (btn) btn.classList.toggle("active", state.pushSubscribed);
    if (icon) icon.innerHTML = state.pushSubscribed ? LUCIDE_ICONS.bell : LUCIDE_ICONS.bellOff;
    if (label) label.textContent = state.pushSubscribed ? "Deal Alerts Active" : "Enable Deal Alerts";
  } catch {
    // Non-fatal
  }
}

async function toggleWebPushSubscription() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    alert("Web Push notifications are not supported in this browser.");
    return;
  }

  try {
    const reg = await navigator.serviceWorker.ready;
    const existingSub = await reg.pushManager.getSubscription();

    if (existingSub) {
      await existingSub.unsubscribe();
      await fetch("/api/push/subscribe", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint: existingSub.endpoint }),
      });
      state.pushSubscribed = false;
    } else {
      const keyRes = await fetch("/api/push/vapid-public-key");
      if (!keyRes.ok) throw new Error("Could not retrieve VAPID key");
      const { publicKey } = await keyRes.json();
      const appKey = urlBase64ToUint8Array(publicKey);

      const newSub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: appKey,
      });

      const subData = newSub.toJSON();
      await fetch("/api/push/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: subData.endpoint,
          keys: subData.keys,
          device_label: navigator.userAgent.includes("Mobile") ? "Mobile Browser" : "Desktop Browser",
        }),
      });
      state.pushSubscribed = true;
      await fetch("/api/push/test", { method: "POST" });
    }
    checkPushSubscriptionState();
  } catch (err) {
    alert(`Web Push subscription error: ${err.message}`);
  }
}

function onToggleWishlistPlatformDetails(itemId, platformFamily, isOpen) {
  if (!state.expandedWishlistPlatforms) state.expandedWishlistPlatforms = new Set();
  const key = `${itemId}:${platformFamily}`;
  if (isOpen) {
    state.expandedWishlistPlatforms.add(key);
  } else {
    state.expandedWishlistPlatforms.delete(key);
  }
}

window.openWishlistAddModal = openWishlistAddModal;
window.openWishlistEditPlatformsModal = openWishlistEditPlatformsModal;
window.openWishlistHistoryModal = openWishlistHistoryModal;
window.openWishlistPurchaseModal = openWishlistPurchaseModal;
window.deleteWishlistItem = deleteWishlistItem;
window.selectCandidateForConfig = selectCandidateForConfig;
window.fetchWishlistAndDeals = fetchWishlistAndDeals;
window.onToggleWishlistPlatformDetails = onToggleWishlistPlatformDetails;

document.addEventListener("DOMContentLoaded", () => {
  applyViewMode(state.viewMode);
  fetchStats();
  switchMainTab(state.activeTab, false);

  // Main View Switcher
  const tabLibraryBtn = document.getElementById("tab-library");
  const tabHabitsBtn = document.getElementById("tab-habits");
  const tabWishlistBtn = document.getElementById("tab-wishlist");
  const mnavLibraryBtn = document.getElementById("mnav-library");
  const mnavHabitsBtn = document.getElementById("mnav-habits");
  const mnavWishlistBtn = document.getElementById("mnav-wishlist");

  if (tabLibraryBtn) tabLibraryBtn.addEventListener("click", () => switchMainTab("library"));
  if (tabHabitsBtn) tabHabitsBtn.addEventListener("click", () => switchMainTab("habits"));
  if (tabWishlistBtn) tabWishlistBtn.addEventListener("click", () => switchMainTab("wishlist"));
  if (mnavLibraryBtn) mnavLibraryBtn.addEventListener("click", () => switchMainTab("library"));
  if (mnavHabitsBtn) mnavHabitsBtn.addEventListener("click", () => switchMainTab("habits"));
  if (mnavWishlistBtn) mnavWishlistBtn.addEventListener("click", () => switchMainTab("wishlist"));

  // Wishlist Actions
  const btnOpenWishlistAdd = document.getElementById("btn-open-wishlist-add");
  if (btnOpenWishlistAdd) btnOpenWishlistAdd.addEventListener("click", openWishlistAddModal);

  const btnCloseWishlistAdd = document.getElementById("btn-close-wishlist-add");
  if (btnCloseWishlistAdd) btnCloseWishlistAdd.addEventListener("click", () => document.getElementById("wishlist-add-dialog")?.close());

  const btnSearchCandidates = document.getElementById("btn-search-candidates");
  if (btnSearchCandidates) btnSearchCandidates.addEventListener("click", searchWishlistCandidates);

  const candidateInput = document.getElementById("wishlist-candidate-input");
  if (candidateInput) {
    candidateInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        searchWishlistCandidates();
      }
    });
  }

  const btnBackCandidates = document.getElementById("btn-back-to-candidates");
  if (btnBackCandidates) {
    btnBackCandidates.addEventListener("click", () => {
      document.getElementById("wishlist-step-search").hidden = false;
      document.getElementById("wishlist-config-form").hidden = true;
    });
  }

  const wishlistConfigForm = document.getElementById("wishlist-config-form");
  if (wishlistConfigForm) wishlistConfigForm.addEventListener("submit", submitWishlistConfig);

  const btnCloseHistory = document.getElementById("btn-close-wishlist-history");
  if (btnCloseHistory) btnCloseHistory.addEventListener("click", () => document.getElementById("wishlist-history-dialog")?.close());

  const btnClosePurchase = document.getElementById("btn-close-wishlist-purchase");
  if (btnClosePurchase) btnClosePurchase.addEventListener("click", () => document.getElementById("wishlist-purchase-dialog")?.close());

  const btnCancelPurchase = document.getElementById("btn-cancel-purchase");
  if (btnCancelPurchase) btnCancelPurchase.addEventListener("click", () => document.getElementById("wishlist-purchase-dialog")?.close());

  const wishlistPurchaseForm = document.getElementById("wishlist-purchase-form");
  if (wishlistPurchaseForm) wishlistPurchaseForm.addEventListener("submit", submitWishlistPurchase);

  const btnSyncWishlist = document.getElementById("btn-sync-wishlist");
  if (btnSyncWishlist) btnSyncWishlist.addEventListener("click", syncWishlistPrices);

  const btnTogglePush = document.getElementById("btn-toggle-push");
  if (btnTogglePush) btnTogglePush.addEventListener("click", toggleWebPushSubscription);

  // Wishlist Filters
  const wStatusFilter = document.getElementById("wishlist-status-filter");
  if (wStatusFilter) {
    wStatusFilter.addEventListener("change", (e) => {
      state.wishlistFilterStatus = e.target.value;
      fetchWishlistAndDeals();
    });
  }

  const wPlatFilter = document.getElementById("wishlist-platform-filter");
  const wPlatPills = document.querySelectorAll("#wishlist-platform-pills .pill");
  const syncWishlistPlatPills = (val) => {
    for (const p of wPlatPills) {
      p.classList.toggle("active", (p.dataset.wishlistPlatform || "") === (val || ""));
    }
  };
  if (wPlatFilter) {
    wPlatFilter.addEventListener("change", (e) => {
      state.wishlistFilterPlatform = e.target.value;
      syncWishlistPlatPills(state.wishlistFilterPlatform);
      renderWishlistGrid();
    });
  }
  for (const pill of wPlatPills) {
    pill.addEventListener("click", () => {
      const val = pill.dataset.wishlistPlatform || "";
      state.wishlistFilterPlatform = val;
      if (wPlatFilter) wPlatFilter.value = val;
      syncWishlistPlatPills(val);
      renderWishlistGrid();
    });
  }

  const wSmartToggle = document.getElementById("wishlist-smart-only-toggle");
  if (wSmartToggle) {
    wSmartToggle.addEventListener("change", (e) => {
      state.wishlistFilterSmartOnly = e.target.checked;
      renderWishlistGrid();
    });
  }

  const wSearchInput = document.getElementById("wishlist-search-input");
  if (wSearchInput) {
    wSearchInput.addEventListener("input", (e) => {
      state.wishlistSearch = e.target.value;
      renderWishlistGrid();
    });
  }

  // Enable drag-to-scroll on mobile-scrollable strips
  enableDragToScroll(document.getElementById("platform-pills"));
  enableDragToScroll(document.getElementById("metrics-bar"));

  // Register Service Worker for PWA installability & offline resilience
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch((err) => {
      console.warn("Service worker registration skipped:", err);
    });
  }

  // PWA Install Banner (Android beforeinstallprompt & iOS Safari hint)
  let deferredInstallPrompt = null;
  const installBanner = document.getElementById("pwa-install-banner");
  const installBtn = document.getElementById("btn-pwa-install");
  const dismissInstallBtn = document.getElementById("btn-pwa-dismiss");
  const installTitle = document.getElementById("pwa-install-title");
  const installSubtext = document.getElementById("pwa-install-subtext");
  const isStandalone =
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;
  const installDismissed = localStorage.getItem("vertex_pwa_dismissed") === "1";

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    if (!isStandalone && !installDismissed && installBanner) {
      if (installTitle) installTitle.textContent = "Install Vertex App";
      if (installSubtext) {
        installSubtext.textContent = "Add to your home screen for quick full-screen library access.";
      }
      if (installBtn) installBtn.hidden = false;
      installBanner.hidden = false;
    }
  });

  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    if (installBanner) installBanner.hidden = true;
  });

  // Detect iOS Safari (non-standalone) or Android plain-HTTP (insecure context) to show helpful hint
  const ua = window.navigator.userAgent || "";
  const isIOS = /iPhone|iPad|iPod/i.test(ua);
  const isAndroid = /Android/i.test(ua);
  if (isIOS && !isStandalone && !installDismissed && installBanner && installSubtext && installBtn) {
    installSubtext.textContent = "Tap Share ⎋ in Safari, then 'Add to Home Screen' for the full-screen app.";
    installBtn.hidden = true;
    installBanner.hidden = false;
  } else if (
    isAndroid &&
    !window.isSecureContext &&
    !isStandalone &&
    !installDismissed &&
    installBanner &&
    installSubtext &&
    installBtn
  ) {
    if (installTitle) installTitle.textContent = "HTTPS Required for Full App Install";
    installSubtext.textContent =
      "Open https://vertex.crjacobs.cc in Chrome to install Vertex as a standalone Android app.";
    installBtn.hidden = true;
    installBanner.hidden = false;
  }

  if (installBtn) {
    installBtn.addEventListener("click", async () => {
      if (!deferredInstallPrompt) return;
      deferredInstallPrompt.prompt();
      await deferredInstallPrompt.userChoice;
      deferredInstallPrompt = null;
      if (installBanner) installBanner.hidden = true;
    });
  }

  if (dismissInstallBtn) {
    dismissInstallBtn.addEventListener("click", () => {
      localStorage.setItem("vertex_pwa_dismissed", "1");
      if (installBanner) installBanner.hidden = true;
    });
  }

  // Filter form prevent default submit
  document.getElementById("filter-form").addEventListener("submit", (e) => {
    e.preventDefault();
  });

  // View Mode Switch (Grid / List)
  const viewGridBtn = document.getElementById("btn-view-grid");
  const viewListBtn = document.getElementById("btn-view-list");
  if (viewGridBtn) {
    viewGridBtn.addEventListener("click", () => applyViewMode("grid"));
  }
  if (viewListBtn) {
    viewListBtn.addEventListener("click", () => applyViewMode("list"));
  }

  // Mobile Filter Drawer Toggle
  const toggleFiltersBtn = document.getElementById("btn-toggle-filters");
  const filterDrawer = document.getElementById("filter-selects-drawer");
  if (toggleFiltersBtn && filterDrawer) {
    toggleFiltersBtn.addEventListener("click", () => {
      const isOpen = filterDrawer.classList.toggle("is-open");
      toggleFiltersBtn.setAttribute("aria-expanded", String(isOpen));
    });
  }

  // Interactive Metric Pills (tap to filter status)
  for (const card of document.querySelectorAll(".metric-card.metric-interactive")) {
    const activateMetricFilter = () => {
      const targetStatus = card.dataset.filterStatus || "";
      state.status = state.status === targetStatus && targetStatus !== "" ? "" : targetStatus;
      const statusSelect = document.getElementById("status-filter");
      if (statusSelect) statusSelect.value = state.status;
      updateActiveFiltersUI();
      refreshActiveView();
    };
    card.addEventListener("click", activateMetricFilter);
    card.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        activateMetricFilter();
      }
    });
  }

  // Reset Filters button
  const resetFiltersBtn = document.getElementById("btn-reset-filters");
  if (resetFiltersBtn) {
    resetFiltersBtn.addEventListener("click", () => {
      state.platform = "";
      state.status = "";
      state.ownership = "";
      state.gamePassOnly = false;
      state.genre = "";
      state.tag = "";
      state.search = "";
      state.sortBy = "playtime_desc";
      state.favoriteOnly = false;

      document.getElementById("search-input").value = "";
      document.getElementById("status-filter").value = "";
      const ofEl = document.getElementById("ownership-filter");
      if (ofEl) ofEl.value = "";
      document.getElementById("genre-filter").value = "";
      const tf = document.getElementById("tag-filter");
      if (tf) tf.value = "";
      document.getElementById("sort-select").value = "playtime_desc";
      document.getElementById("btn-fav-toggle").setAttribute("aria-pressed", "false");
      document.getElementById("btn-gamepass-toggle")?.setAttribute("aria-pressed", "false");
      for (const p of document.querySelectorAll("#platform-pills .pill")) {
        p.classList.toggle("active", p.dataset.platform === "");
      }
      document.querySelector('#platform-pills .pill[data-platform=""]')?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
      refreshActiveView();
    });
  }

  // Search input
  let searchTimer = null;
  document.getElementById("search-input").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.search = e.target.value.trim();
      refreshActiveView();
    }, 200);
  });

  // Platform pills
  document.getElementById("platform-pills").addEventListener("click", (e) => {
    const btn = e.target.closest(".pill");
    if (!btn) return;
    for (const p of document.querySelectorAll("#platform-pills .pill")) {
      p.classList.remove("active");
    }
    btn.classList.add("active");
    btn.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
    state.platform = btn.dataset.platform || "";
    refreshActiveView();
  });

  // Status, Genre & Sort selects
  const genreFilterEl = document.getElementById("genre-filter");
  if (genreFilterEl) {
    genreFilterEl.addEventListener("change", (e) => {
      state.genre = e.target.value;
      refreshActiveView();
    });
  }

  const tagFilterEl = document.getElementById("tag-filter");
  if (tagFilterEl) {
    tagFilterEl.addEventListener("change", (e) => {
      state.tag = e.target.value;
      refreshActiveView();
    });
  }

  document.getElementById("status-filter").addEventListener("change", (e) => {
    state.status = e.target.value;
    refreshActiveView();
  });

  const ownershipFilterEl = document.getElementById("ownership-filter");
  if (ownershipFilterEl) {
    ownershipFilterEl.addEventListener("change", (e) => {
      state.ownership = e.target.value;
      refreshActiveView();
    });
  }

  document.getElementById("sort-select").addEventListener("change", (e) => {
    state.sortBy = e.target.value;
    refreshActiveView();
  });

  // Favorites toggle
  const favBtn = document.getElementById("btn-fav-toggle");
  favBtn.addEventListener("click", () => {
    state.favoriteOnly = !state.favoriteOnly;
    favBtn.setAttribute("aria-pressed", String(state.favoriteOnly));
    refreshActiveView();
  });

  // Game Pass toggle
  const gpFilterBtn = document.getElementById("btn-gamepass-toggle");
  if (gpFilterBtn) {
    gpFilterBtn.addEventListener("click", () => {
      state.gamePassOnly = !state.gamePassOnly;
      gpFilterBtn.setAttribute("aria-pressed", String(state.gamePassOnly));
      refreshActiveView();
    });
  }

  // Trigger Sync (Header + Mobile Bottom Nav)
  const syncBtn = document.getElementById("btn-sync");
  const mnavSyncBtn = document.getElementById("mnav-sync");
  const mnavSyncIcon = document.getElementById("mnav-sync-icon");
  const mnavSyncLabel = document.getElementById("mnav-sync-label");

  async function triggerLibrarySync() {
    if (syncBtn) {
      syncBtn.disabled = true;
      syncBtn.classList.add("sync-running");
      const icon = syncBtn.querySelector(".lucide-icon");
      if (icon) icon.classList.add("spin-anim");
    }
    if (mnavSyncBtn) mnavSyncBtn.disabled = true;
    if (mnavSyncIcon) mnavSyncIcon.classList.add("spin-anim");
    if (mnavSyncLabel) mnavSyncLabel.textContent = "Syncing";
    try {
      await fetch("/api/sync", { method: "POST" });
      await fetchStats();
      await fetchGames();
    } finally {
      if (syncBtn) {
        syncBtn.disabled = false;
        syncBtn.classList.remove("sync-running");
        const icon = syncBtn.querySelector(".lucide-icon");
        if (icon) icon.classList.remove("spin-anim");
      }
      if (mnavSyncBtn) mnavSyncBtn.disabled = false;
      if (mnavSyncIcon) mnavSyncIcon.classList.remove("spin-anim");
      if (mnavSyncLabel) mnavSyncLabel.textContent = "Sync";
    }
  }

  if (syncBtn) syncBtn.addEventListener("click", triggerLibrarySync);
  if (mnavSyncBtn) {
    mnavSyncBtn.addEventListener("click", triggerLibrarySync);
  }

  // Contextual Actions Sheet
  const btnLibraryActions = document.getElementById("btn-library-actions");
  const actionsDialog = document.getElementById("library-actions-dialog");
  if (btnLibraryActions && actionsDialog) {
    btnLibraryActions.addEventListener("click", () => {
      actionsDialog.showModal();
    });
  }

  // Add Game Dialog
  const addDialog = document.getElementById("add-game-dialog");
  function openAddGameModal() {
    document.getElementById("add-game-form").reset();
    syncAddFavButton(false);
    document.getElementById("catalog-results").hidden = true;
    addDialog.showModal();
  }
  document.getElementById("btn-open-add").addEventListener("click", openAddGameModal);
  document.getElementById("btn-close-add").addEventListener("click", () => addDialog.close());
  document.getElementById("btn-cancel-add").addEventListener("click", () => addDialog.close());

  const addFavBtn = document.getElementById("btn-add-fav");
  if (addFavBtn) {
    addFavBtn.addEventListener("click", () => {
      const favCheck = document.getElementById("add-favorite");
      syncAddFavButton(!favCheck.checked);
    });
  }

  document.getElementById("btn-catalog-lookup").addEventListener("click", handleCatalogSearch);
  document.getElementById("catalog-search-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleCatalogSearch();
    }
  });

  document.getElementById("add-game-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const genresRaw = document.getElementById("add-genres").value;
    const genres = genresRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const tagsRaw = document.getElementById("add-tags")?.value || "";
    const tags = tagsRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const ratingVal = document.getElementById("add-rating").value;

    const payload = {
      title: document.getElementById("add-title").value.trim(),
      platform: document.getElementById("add-platform").value,
      completion_status: document.getElementById("add-status").value,
      ownership_status: document.getElementById("add-ownership")?.value || "Owned",
      is_game_pass: Boolean(document.getElementById("add-gamepass")?.checked),
      playtime_hours: parseFloat(document.getElementById("add-hours").value || "0"),
      user_rating: ratingVal ? parseInt(ratingVal, 10) : null,
      cover_url: document.getElementById("add-cover").value.trim() || null,
      genres,
      tags,
      notes: document.getElementById("add-notes").value.trim(),
      favorite: Boolean(document.getElementById("add-favorite")?.checked),
    };

    const res = await fetch("/api/games", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      addDialog.close();
      await fetchStats();
      await fetchGames();
    }
  });

  // Edit Game Dialog
  const editDialog = document.getElementById("edit-game-dialog");
  document.getElementById("btn-close-edit").addEventListener("click", () => editDialog.close());
  document.getElementById("btn-cancel-edit").addEventListener("click", () => editDialog.close());

  const editFavBtn = document.getElementById("btn-edit-fav");
  if (editFavBtn) {
    editFavBtn.addEventListener("click", () => {
      const favCheck = document.getElementById("edit-favorite");
      syncEditFavButton(!favCheck.checked);
    });
  }

  const clearRatingBtn = document.getElementById("btn-clear-rating");
  if (clearRatingBtn) {
    clearRatingBtn.addEventListener("click", () => {
      renderEditRatingPills(null);
    });
  }

  const editGenrePicker = document.getElementById("edit-genre-picker");
  if (editGenrePicker) {
    editGenrePicker.addEventListener("change", (e) => {
      const chosen = e.target.value;
      if (!chosen) return;
      const inputEl = document.getElementById("edit-genres");
      const current = inputEl.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (!current.some((g) => g.toLowerCase() === chosen.toLowerCase())) {
        current.push(chosen);
        inputEl.value = current.join(", ");
      }
      renderEditGenreChips();
    });
  }

  const editTagPicker = document.getElementById("edit-tag-picker");
  if (editTagPicker) {
    editTagPicker.addEventListener("change", (e) => {
      const chosen = e.target.value;
      if (!chosen) return;
      const inputEl = document.getElementById("edit-tags");
      const current = inputEl.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (!current.some((t) => t.toLowerCase() === chosen.toLowerCase())) {
        current.push(chosen);
        inputEl.value = current.join(", ");
      }
      renderEditTagChips();
    });
  }

  const editCoverInput = document.getElementById("edit-cover");
  if (editCoverInput) {
    editCoverInput.addEventListener("input", (e) => {
      const val = e.target.value.trim();
      const preview = document.getElementById("edit-cover-preview");
      if (preview && val) {
        preview.src = val;
      }
    });
  }

  const toggleHoursBtn = document.getElementById("btn-toggle-edit-hours");
  if (toggleHoursBtn) {
    toggleHoursBtn.addEventListener("click", () => {
      const hoursInput = document.getElementById("edit-hours");
      const hoursDisplay = document.getElementById("edit-hours-display");
      const hoursEditWrap = document.getElementById("edit-hours-edit-wrap");
      const isUnlocked = hoursInput.dataset.unlocked === "true";
      if (!isUnlocked) {
        hoursInput.dataset.unlocked = "true";
        hoursInput.readOnly = false;
        hoursDisplay.hidden = true;
        hoursEditWrap.hidden = false;
        toggleHoursBtn.textContent = "Lock";
        hoursInput.focus();
        hoursInput.select();
      } else {
        hoursInput.dataset.unlocked = "false";
        hoursInput.readOnly = true;
        hoursDisplay.textContent = `⏱ ${parseFloat(hoursInput.value || "0")} hrs`;
        hoursDisplay.hidden = false;
        hoursEditWrap.hidden = true;
        toggleHoursBtn.textContent = "Edit";
      }
    });
  }

  const toggleAchBtn = document.getElementById("btn-toggle-edit-ach");
  if (toggleAchBtn) {
    toggleAchBtn.addEventListener("click", () => {
      const achWrap = document.getElementById("edit-ach-edit-wrap");
      const achDisplay = document.getElementById("edit-ach-display");
      const isEditing = achWrap && !achWrap.hidden;
      if (!isEditing) {
        if (achWrap) achWrap.hidden = false;
        if (achDisplay) achDisplay.hidden = true;
        toggleAchBtn.textContent = "Cancel";
        const unlInput = document.getElementById("edit-ach-unlocked");
        if (unlInput) {
          unlInput.focus();
          unlInput.select();
        }
      } else {
        if (achWrap) achWrap.hidden = true;
        if (achDisplay) achDisplay.hidden = false;
        toggleAchBtn.textContent = "Edit";
      }
    });
  }

  document.getElementById("edit-game-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const gameId = document.getElementById("edit-id").value;
    const genresRaw = document.getElementById("edit-genres").value;
    const genres = genresRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const tagsRaw = document.getElementById("edit-tags")?.value || "";
    const tags = tagsRaw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const ratingVal = document.getElementById("edit-rating").value;
    const hoursInput = document.getElementById("edit-hours");

    const payload = {
      completion_status: document.getElementById("edit-status").value,
      user_rating: ratingVal ? parseInt(ratingVal, 10) : null,
      genres,
      tags,
      cover_url: document.getElementById("edit-cover").value.trim() || null,
      notes: document.getElementById("edit-notes").value.trim(),
      favorite: document.getElementById("edit-favorite").checked,
    };

    const editOwnEl = document.getElementById("edit-ownership");
    if (editOwnEl && !editOwnEl.disabled) {
      payload.ownership_status = editOwnEl.value || "Owned";
    }

    const editGpEl = document.getElementById("edit-gamepass");
    if (editGpEl && !editGpEl.disabled) {
      payload.is_game_pass = editGpEl.checked;
    }

    if (hoursInput && hoursInput.dataset.unlocked === "true") {
      payload.playtime_hours = parseFloat(hoursInput.value || "0");
    }

    const achWrap = document.getElementById("edit-ach-edit-wrap");
    const achUnlInput = document.getElementById("edit-ach-unlocked");
    const achTotInput = document.getElementById("edit-ach-total");
    if (achWrap && !achWrap.hidden && achUnlInput && achTotInput) {
      const u = parseInt(achUnlInput.value, 10);
      const t = parseInt(achTotInput.value, 10);
      if (!Number.isNaN(u)) payload.achievements_unlocked = Math.max(0, u);
      if (!Number.isNaN(t)) payload.achievements_total = Math.max(0, t);
    }

    const res = await fetch(`/api/games/${gameId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (res.ok) {
      editDialog.close();
      await fetchStats();
      await fetchGames();
    }
  });

  document.getElementById("btn-delete-game").addEventListener("click", async () => {
    const gameId = document.getElementById("edit-id").value;
    if (!confirm("Remove this game from your library?")) return;
    const res = await fetch(`/api/games/${gameId}`, { method: "DELETE" });
    if (res.ok) {
      editDialog.close();
      await fetchStats();
      await fetchGames();
    }
  });

  // Quick-link another game from inside the Edit Game modal
  let editLinkTimer = null;
  const editLinkInput = document.getElementById("edit-link-search");
  const editLinkResults = document.getElementById("edit-link-results");
  editLinkInput.addEventListener("input", (e) => {
    clearTimeout(editLinkTimer);
    const q = e.target.value.trim();
    if (q.length < 2) {
      editLinkResults.hidden = true;
      editLinkResults.innerHTML = "";
      return;
    }
    editLinkTimer = setTimeout(async () => {
      const currentId = parseInt(document.getElementById("edit-id").value, 10);
      const res = await fetch(`/api/games?search=${encodeURIComponent(q)}&limit=10`);
      if (!res.ok) return;
      const data = await res.json();
      const matches = (data.games || []).filter((g) => g.id !== currentId);
      editLinkResults.innerHTML = "";
      if (matches.length === 0) {
        editLinkResults.hidden = true;
        return;
      }
      editLinkResults.hidden = false;
      for (const m of matches) {
        const li = document.createElement("li");
        li.className = "catalog-item";
        li.style.justifyContent = "space-between";

        const info = document.createElement("span");
        info.textContent = `${m.title} (${m.platform} • ${m.playtime_hours || 0}h)`;

        const linkBtn = document.createElement("button");
        linkBtn.type = "button";
        linkBtn.className = "btn btn-primary btn-xs";
        linkBtn.textContent = "+ Link";
        linkBtn.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const confirmRes = await fetch("/api/reconcile/confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              game_ids: [currentId, m.id],
              primary_id: currentId,
            }),
          });
          if (confirmRes.ok) {
            const cData = await confirmRes.json();
            await fetchStats();
            await fetchGames();
            if (cData.game) {
              openEditModal(cData.game);
            }
          }
        });

        li.appendChild(info);
        li.appendChild(linkBtn);
        editLinkResults.appendChild(li);
      }
    }, 180);
  });

  // Achievements Explorer Listeners inside Edit Game Modal
  const refreshGameAchBtn = document.getElementById("btn-refresh-game-ach");
  if (refreshGameAchBtn) {
    refreshGameAchBtn.addEventListener("click", () => {
      const idVal = parseInt(document.getElementById("edit-id").value, 10);
      if (idVal) {
        loadAndRenderGameAchievements(idVal, true);
      }
    });
  }

  document.querySelectorAll(".ach-pill[data-ach-filter]").forEach((pill) => {
    pill.addEventListener("click", () => {
      document.querySelectorAll(".ach-pill[data-ach-filter]").forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      state.currentAchFilter = pill.dataset.achFilter;
      renderAchievementsList();
    });
  });

  const achSearchInput = document.getElementById("ach-search-input");
  let achSearchTimer = null;
  if (achSearchInput) {
    achSearchInput.addEventListener("input", (e) => {
      clearTimeout(achSearchTimer);
      achSearchTimer = setTimeout(() => {
        state.currentAchSearch = (e.target.value || "").trim().toLowerCase();
        renderAchievementsList();
      }, 150);
    });
  }

  // Cross-Platform Reconcile Dialog & Hotkeys
  const reconcileDialog = document.getElementById("reconcile-dialog");
  document.getElementById("btn-open-reconcile").addEventListener("click", openReconcileModal);
  document.getElementById("btn-close-reconcile").addEventListener("click", () => {
    reconcileDialog.close();
  });
  reconcileDialog.addEventListener("close", () => {
    fetchGames();
    fetchStats();
  });

  document.getElementById("reconcile-scope").addEventListener("change", (e) => {
    reloadReconcileQueue(e.target.value);
  });

  document.getElementById("btn-reconcile-confirm").addEventListener("click", confirmCurrentReconcileGroup);
  document.getElementById("btn-reconcile-dismiss").addEventListener("click", dismissCurrentReconcileGroup);
  document.getElementById("btn-reconcile-prev").addEventListener("click", () => stepReconcile(-1));
  document.getElementById("btn-reconcile-skip").addEventListener("click", () => stepReconcile(1));

  document.getElementById("btn-reconcile-open-manual").addEventListener("click", () => {
    const scopeSelect = document.getElementById("reconcile-scope");
    scopeSelect.value = "manual";
    reloadReconcileQueue("manual");
  });

  document.getElementById("btn-reconcile-reset-dismissed").addEventListener("click", async () => {
    const res = await fetch("/api/reconcile/reset-dismissals", { method: "POST" });
    if (res.ok) {
      await fetchStats();
      await reloadReconcileQueue(state.reconcileScope);
      showReconcileToast("Restored dismissed suggestions");
    }
  });

  let manualReconcileTimer = null;
  document.getElementById("manual-reconcile-search").addEventListener("input", (e) => {
    clearTimeout(manualReconcileTimer);
    manualReconcileTimer = setTimeout(() => {
      renderManualSearchResults(e.target.value.trim());
    }, 180);
  });

  // Quick Triage Dialog & Hotkeys
  const triageDialog = document.getElementById("triage-dialog");
  document.getElementById("btn-open-triage").addEventListener("click", openTriageModal);
  document.getElementById("btn-close-triage").addEventListener("click", () => {
    triageDialog.close();
  });
  document.getElementById("btn-done-triage").addEventListener("click", () => {
    triageDialog.close();
  });
  triageDialog.addEventListener("close", () => {
    fetchGames();
    fetchStats();
  });

  document.getElementById("triage-scope").addEventListener("change", (e) => {
    reloadTriageQueue(e.target.value);
  });

  for (const btn of document.querySelectorAll(".triage-action-btn")) {
    btn.addEventListener("click", () => {
      applyTriageStatus(btn.dataset.status);
    });
  }

  document.getElementById("btn-triage-fav").addEventListener("click", toggleTriageFavorite);
  const triageGpBtn = document.getElementById("btn-triage-gamepass");
  if (triageGpBtn) {
    triageGpBtn.addEventListener("click", toggleTriageGamePass);
  }
  const triageOwnBtn = document.getElementById("btn-triage-ownership");
  if (triageOwnBtn) {
    triageOwnBtn.addEventListener("click", toggleTriageOwnership);
  }
  document.getElementById("btn-triage-prev").addEventListener("click", () => stepTriage(-1));
  document.getElementById("btn-triage-skip").addEventListener("click", () => stepTriage(1));

  // Touch swipe navigation on Triage & Reconcile cards (swipe left = next/skip, swipe right = previous)
  attachHorizontalSwipe(
    document.getElementById("triage-card"),
    () => stepTriage(1),
    () => stepTriage(-1)
  );
  attachHorizontalSwipe(
    document.getElementById("reconcile-card"),
    () => stepReconcile(1),
    () => stepReconcile(-1)
  );

  // Mobile Bottom Navigation Bar wiring
  const mnavLibrary = document.getElementById("mnav-library");
  const mnavTriage = document.getElementById("mnav-triage");
  const mnavAdd = document.getElementById("mnav-add");
  const mnavReconcile = document.getElementById("mnav-reconcile");
  const mnavSettings = document.getElementById("mnav-settings");

  function setMobileNavActive(activeId) {
    for (const item of document.querySelectorAll(".mobile-bottom-nav .mnav-item")) {
      item.classList.toggle("active", item.id === activeId);
    }
  }

  if (mnavLibrary) {
    mnavLibrary.addEventListener("click", () => {
      for (const d of document.querySelectorAll("dialog[open]")) {
        d.close();
      }
      setMobileNavActive("mnav-library");
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
  if (mnavTriage) {
    mnavTriage.addEventListener("click", () => {
      setMobileNavActive("mnav-triage");
      openTriageModal();
    });
  }
  if (mnavAdd) {
    mnavAdd.addEventListener("click", () => {
      openAddGameModal();
    });
  }
  if (mnavReconcile) {
    mnavReconcile.addEventListener("click", () => {
      setMobileNavActive("mnav-reconcile");
      openReconcileModal();
    });
  }
  if (mnavSettings) {
    mnavSettings.addEventListener("click", () => {
      setMobileNavActive("mnav-settings");
      openSettingsModal();
    });
  }

  triageDialog.addEventListener("close", () => setMobileNavActive("mnav-library"));
  reconcileDialog.addEventListener("close", () => setMobileNavActive("mnav-library"));
  addDialog.addEventListener("close", () => setMobileNavActive("mnav-library"));

  // Settings & Connections Dialog
  const settingsDialog = document.getElementById("settings-dialog");
  const btnOpenSettings = document.getElementById("btn-open-settings");
  if (btnOpenSettings) {
    btnOpenSettings.addEventListener("click", openSettingsModal);
  }
  const btnMobileSettings = document.getElementById("btn-mobile-settings");
  if (btnMobileSettings) {
    btnMobileSettings.addEventListener("click", openSettingsModal);
  }
  const btnMobileSync = document.getElementById("btn-mobile-sync");
  if (btnMobileSync) {
    btnMobileSync.addEventListener("click", () => {
      const syncBtn = document.getElementById("btn-sync");
      if (syncBtn) syncBtn.click();
    });
  }
  const btnCloseSettings = document.getElementById("btn-close-settings");
  if (btnCloseSettings && settingsDialog) {
    btnCloseSettings.addEventListener("click", () => settingsDialog.close());
  }
  const btnCloseSettingsFooter = document.getElementById("btn-close-settings-footer");
  if (btnCloseSettingsFooter && settingsDialog) {
    btnCloseSettingsFooter.addEventListener("click", () => settingsDialog.close());
  }
  if (settingsDialog) {
    settingsDialog.addEventListener("close", () => setMobileNavActive("mnav-library"));
  }

  const stabConn = document.getElementById("stab-connections");
  if (stabConn) stabConn.addEventListener("click", () => switchSettingsTab("stab-connections"));
  const stabMcp = document.getElementById("stab-mcp");
  if (stabMcp) stabMcp.addEventListener("click", () => switchSettingsTab("stab-mcp"));
  const stabPort = document.getElementById("stab-portability");
  if (stabPort) stabPort.addEventListener("click", () => switchSettingsTab("stab-portability"));
  const stabAccount = document.getElementById("stab-account");
  if (stabAccount) stabAccount.addEventListener("click", () => {
    switchSettingsTab("stab-account");
    loadAccountInfo();
  });
  const stabAdminUsers = document.getElementById("stab-admin-users");
  if (stabAdminUsers) stabAdminUsers.addEventListener("click", () => {
    switchSettingsTab("stab-admin-users");
    loadAdminUsers();
  });

  const btnSubmitPw = document.getElementById("btn-submit-change-password");
  if (btnSubmitPw) btnSubmitPw.addEventListener("click", saveAccountPassword);

  const btnToggleCreateUser = document.getElementById("btn-toggle-create-user");
  const adminCreateCard = document.getElementById("admin-create-user-card");
  if (btnToggleCreateUser && adminCreateCard) {
    btnToggleCreateUser.addEventListener("click", () => {
      adminCreateCard.hidden = !adminCreateCard.hidden;
    });
  }
  const btnCancelCreateUser = document.getElementById("btn-cancel-create-user");
  if (btnCancelCreateUser && adminCreateCard) {
    btnCancelCreateUser.addEventListener("click", () => {
      adminCreateCard.hidden = true;
    });
  }
  const btnSubmitCreateUser = document.getElementById("btn-submit-create-user");
  if (btnSubmitCreateUser) btnSubmitCreateUser.addEventListener("click", createAdminUser);

  const btnSavePolicy = document.getElementById("btn-save-reg-policy");
  if (btnSavePolicy) btnSavePolicy.addEventListener("click", saveAdminRegPolicy);

  const btnCreateInvite = document.getElementById("btn-create-invite");
  if (btnCreateInvite) btnCreateInvite.addEventListener("click", createAdminInvite);

  const btnCopyInviteLink = document.getElementById("btn-copy-invite-link");
  if (btnCopyInviteLink) {
    btnCopyInviteLink.addEventListener("click", async () => {
      const inp = document.getElementById("admin-invite-link-input");
      if (!inp || !inp.value) return;
      try {
        await navigator.clipboard.writeText(inp.value);
        btnCopyInviteLink.textContent = "Copied!";
        setTimeout(() => { btnCopyInviteLink.textContent = "Copy Link"; }, 2000);
      } catch (e) {}
    });
  }



  const btnToggleXbl = document.getElementById("btn-toggle-xbl");
  if (btnToggleXbl) {
    btnToggleXbl.addEventListener("click", () => {
      const inp = document.getElementById("setting-xbl-key");
      if (!inp) return;
      const isPw = inp.type === "password";
      inp.type = isPw ? "text" : "password";
      btnToggleXbl.textContent = isPw ? "Hide" : "Show";
    });
  }

  const btnTogglePsn = document.getElementById("btn-toggle-psn");
  if (btnTogglePsn) {
    btnTogglePsn.addEventListener("click", () => {
      const inp = document.getElementById("setting-psn-npsso");
      if (!inp) return;
      const isPw = inp.type === "password";
      inp.type = isPw ? "text" : "password";
      btnTogglePsn.textContent = isPw ? "Hide" : "Show";
    });
  }

  const btnSaveConn = document.getElementById("btn-save-connections");
  if (btnSaveConn) {
    btnSaveConn.addEventListener("click", saveSettingsConnections);
  }

  const btnCopyMcp = document.getElementById("btn-copy-mcp-endpoint");
  if (btnCopyMcp) {
    btnCopyMcp.addEventListener("click", async () => {
      const inp = document.getElementById("mcp-endpoint-input");
      if (!inp || !inp.value) return;
      try {
        await navigator.clipboard.writeText(inp.value);
        btnCopyMcp.textContent = "Copied!";
        setTimeout(() => { btnCopyMcp.textContent = "Copy URL"; }, 2000);
      } catch (e) {
        console.error("Clipboard copy failed:", e);
      }
    });
  }

  const btnGenTok = document.getElementById("btn-generate-token");
  if (btnGenTok) {
    btnGenTok.addEventListener("click", generateMcpToken);
  }

  const btnCopyNewTok = document.getElementById("btn-copy-new-token");
  if (btnCopyNewTok) {
    btnCopyNewTok.addEventListener("click", async () => {
      const inp = document.getElementById("new-token-value");
      if (!inp || !inp.value) return;
      try {
        await navigator.clipboard.writeText(inp.value);
        btnCopyNewTok.textContent = "Copied!";
        setTimeout(() => { btnCopyNewTok.textContent = "Copy Token"; }, 2000);
      } catch (e) {
        console.error("Clipboard copy failed:", e);
      }
    });
  }

  const btnTriggerImport = document.getElementById("btn-trigger-import-db");
  const fileImportInput = document.getElementById("import-db-file");
  if (btnTriggerImport && fileImportInput) {
    btnTriggerImport.addEventListener("click", () => fileImportInput.click());
    fileImportInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length) {
        handleDatabaseImport(e.target.files[0]);
      }
      e.target.value = "";
    });
  }

  // Floating Scroll-to-Top Button
  const scrollTopBtn = document.getElementById("btn-scroll-top");
  if (scrollTopBtn) {
    let isHidden = true;
    let ticking = false;
    window.addEventListener(
      "scroll",
      () => {
        if (!ticking) {
          window.requestAnimationFrame(() => {
            const shouldHide = window.scrollY < 520;
            if (isHidden !== shouldHide) {
              isHidden = shouldHide;
              scrollTopBtn.hidden = shouldHide;
            }
            ticking = false;
          });
          ticking = true;
        }
      },
      { passive: true }
    );
    scrollTopBtn.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  // Onboarding Wizard & Verification Button Bindings
  const btnOpenConn = document.getElementById("btn-open-connections");
  if (btnOpenConn) btnOpenConn.addEventListener("click", openConnectionsModal);

  const btnWelcomeConn = document.getElementById("btn-welcome-connect");
  if (btnWelcomeConn) btnWelcomeConn.addEventListener("click", openOnboardingWizard);

  const btnLaunchWiz = document.getElementById("btn-launch-wizard");
  if (btnLaunchWiz) btnLaunchWiz.addEventListener("click", openOnboardingWizard);

  const btnCloseWiz = document.getElementById("btn-close-onboarding");
  if (btnCloseWiz) btnCloseWiz.addEventListener("click", closeOnboardingWizard);

  const btnWizPrev = document.getElementById("btn-wizard-prev");
  if (btnWizPrev) btnWizPrev.addEventListener("click", () => switchWizardStep(currentWizardStep - 1));

  const btnWizNext = document.getElementById("btn-wizard-next");
  if (btnWizNext) btnWizNext.addEventListener("click", () => switchWizardStep(currentWizardStep + 1));

  const btnWizDismiss = document.getElementById("btn-wizard-dismiss-forever");
  if (btnWizDismiss) btnWizDismiss.addEventListener("click", dismissWizardForever);

  const btnWizSkip = document.getElementById("btn-wizard-skip");
  if (btnWizSkip) btnWizSkip.addEventListener("click", skipWizard);

  const btnWizStartSync = document.getElementById("btn-wizard-start-sync");
  if (btnWizStartSync) btnWizStartSync.addEventListener("click", finishWizardAndSync);

  const btnVerifySteam = document.getElementById("btn-verify-steam");
  if (btnVerifySteam) {
    btnVerifySteam.addEventListener("click", () => {
      const val = document.getElementById("wizard-steam-id")?.value || "";
      testPlatformConnection("steam", val, "wizard-steam-status");
    });
  }

  const btnVerifyXbox = document.getElementById("btn-verify-xbox");
  if (btnVerifyXbox) {
    btnVerifyXbox.addEventListener("click", () => {
      clearTimeout(xboxLookupTimer);
      const val = document.getElementById("wizard-xbl-key")?.value || "";
      performLiveXboxLink(val, "wizard-xbox-status", true);
    });
  }

  const wizXblInp = document.getElementById("wizard-xbl-key");
  if (wizXblInp) {
    wizXblInp.addEventListener("input", () => {
      clearTimeout(xboxLookupTimer);
      const val = wizXblInp.value.trim();
      if (val.length < 2) {
        const container = document.getElementById("wizard-xbox-status");
        if (container) {
          container.hidden = true;
          container.innerHTML = "";
        }
        return;
      }
      xboxLookupTimer = setTimeout(() => {
        performLiveXboxLink(val, "wizard-xbox-status", true);
      }, 500);
    });

    wizXblInp.addEventListener("keydown", async (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        clearTimeout(xboxLookupTimer);
        const val = wizXblInp.value.trim();
        if (val) {
          const ok = await performLiveXboxLink(val, "wizard-xbox-status", true);
          if (ok) {
            setTimeout(() => switchWizardStep(3), 600);
          }
        } else {
          switchWizardStep(3);
        }
      }
    });
  }

  const btnVerifyPsn = document.getElementById("btn-verify-psn");
  if (btnVerifyPsn) {
    btnVerifyPsn.addEventListener("click", () => {
      const val = document.getElementById("wizard-psn-token")?.value || "";
      testPlatformConnection("psn", val, "wizard-psn-status");
    });
  }

  const btnTestSetSteam = document.getElementById("btn-test-settings-steam");
  if (btnTestSetSteam) {
    btnTestSetSteam.addEventListener("click", () => {
      const val = document.getElementById("setting-steam-id")?.value || "";
      testPlatformConnection("steam", val, "settings-steam-status");
    });
  }

  const btnTestSetXbox = document.getElementById("btn-test-settings-xbox");
  if (btnTestSetXbox) {
    btnTestSetXbox.addEventListener("click", () => {
      clearTimeout(xboxLookupTimer);
      const val = document.getElementById("setting-xbl-key")?.value || "";
      performLiveXboxLink(val, "settings-xbox-status", false);
    });
  }

  const settingXblInp = document.getElementById("setting-xbl-key");
  if (settingXblInp) {
    settingXblInp.addEventListener("input", () => {
      clearTimeout(xboxLookupTimer);
      const val = settingXblInp.value.trim();
      if (val.length < 2) {
        const container = document.getElementById("settings-xbox-status");
        if (container) {
          container.hidden = true;
          container.innerHTML = "";
        }
        return;
      }
      xboxLookupTimer = setTimeout(() => {
        performLiveXboxLink(val, "settings-xbox-status", false);
      }, 500);
    });

    settingXblInp.addEventListener("keydown", async (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        clearTimeout(xboxLookupTimer);
        const val = settingXblInp.value.trim();
        if (val) {
          await performLiveXboxLink(val, "settings-xbox-status", false);
        }
      }
    });
  }

  const btnDisconnectXbox = document.getElementById("btn-disconnect-xbox");
  if (btnDisconnectXbox) {
    btnDisconnectXbox.addEventListener("click", () => {
      disconnectXboxAccount();
    });
  }

  const btnTestSetPsn = document.getElementById("btn-test-settings-psn");
  if (btnTestSetPsn) {
    btnTestSetPsn.addEventListener("click", () => {
      const val = document.getElementById("setting-psn-npsso")?.value || "";
      testPlatformConnection("psn", val, "settings-psn-status");
    });
  }

  const btnToggleWizXbl = document.getElementById("btn-toggle-wizard-xbl");
  if (btnToggleWizXbl) {
    btnToggleWizXbl.addEventListener("click", () => {
      const inp = document.getElementById("wizard-xbl-key");
      if (!inp) return;
      const isPw = inp.type === "password";
      inp.type = isPw ? "text" : "password";
      btnToggleWizXbl.textContent = isPw ? "Hide" : "Show";
    });
  }

  const btnToggleWizPsn = document.getElementById("btn-toggle-wizard-psn");
  if (btnToggleWizPsn) {
    btnToggleWizPsn.addEventListener("click", () => {
      const inp = document.getElementById("wizard-psn-token");
      if (!inp) return;
      const isPw = inp.type === "password";
      inp.type = isPw ? "text" : "password";
      btnToggleWizPsn.textContent = isPw ? "Hide" : "Show";
    });
  }

  // Handle PWA Home Screen Shortcut URLs (?action=add | ?action=triage | ?action=reconcile)
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get("steam_connected") === "1") {
    const cleanUrl = window.location.pathname;
    window.history.replaceState({}, document.title, cleanUrl);
    setTimeout(() => {
      const syncBtn = document.getElementById("btn-sync");
      if (syncBtn) syncBtn.click();
    }, 400);
  } else if (urlParams.get("error") === "steam_verification_failed") {
    alert("Steam connection verification failed. Please try again or enter your Steam ID manually.");
    const cleanUrl = window.location.pathname;
    window.history.replaceState({}, document.title, cleanUrl);
  }

  fetch("/api/user/connections").then(async (res) => {
    if (res.ok) {
      const conns = await res.json();
      if (conns && conns.onboarding_completed === false) {
        if (!localStorage.getItem("vertex_onboarding_dismissed") && !sessionStorage.getItem("vertex_onboarding_dismissed")) {
          setTimeout(() => openOnboardingWizard(), 400);
        }
      }
    }
  }).catch(() => {});
  const actionParam = urlParams.get("action");
  if (actionParam === "add") {
    openAddGameModal();
    window.history.replaceState({}, "", "/");
  } else if (actionParam === "triage") {
    openTriageModal();
    window.history.replaceState({}, "", "/");
  } else if (actionParam === "reconcile") {
    openReconcileModal();
    window.history.replaceState({}, "", "/");
  }

  // Global keyboard shortcuts while Quick Triage or Reconcile modal is open
  const keyToStatus = {
    "1": "Playing",
    "2": "Plan to Play",
    "3": "Beaten",
    "4": "Completed",
    "5": "Dropped",
    "6": "Not Played",
  };

  window.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (reconcileDialog.open && state.reconcileScope !== "manual") {
      if (e.key === "Enter" || e.key === "c" || e.key === "C") {
        e.preventDefault();
        confirmCurrentReconcileGroup();
      } else if (e.key === "x" || e.key === "X" || e.key === "d" || e.key === "D") {
        e.preventDefault();
        dismissCurrentReconcileGroup();
      } else if (e.key === "ArrowRight" || e.key === " ") {
        e.preventDefault();
        stepReconcile(1);
      } else if (e.key === "ArrowLeft" || e.key === "Backspace") {
        e.preventDefault();
        stepReconcile(-1);
      }
      return;
    }

    if (!triageDialog.open) return;

    if (keyToStatus[e.key]) {
      e.preventDefault();
      applyTriageStatus(keyToStatus[e.key]);
    } else if (e.key === "g" || e.key === "G") {
      e.preventDefault();
      toggleTriageGamePass();
    } else if (e.key === "o" || e.key === "O") {
      e.preventDefault();
      toggleTriageOwnership();
    } else if (e.key === "f" || e.key === "F") {
      e.preventDefault();
      toggleTriageFavorite();
    } else if (e.key === "ArrowRight" || e.key === " ") {
      e.preventDefault();
      stepTriage(1);
    } else if (e.key === "ArrowLeft" || e.key === "Backspace") {
      e.preventDefault();
      stepTriage(-1);
    }
  });

  // Fallback for <dialog closedby="any"> light-dismiss in browsers without native closedBy support
  if (!("closedBy" in HTMLDialogElement.prototype)) {
    const outside = (e, dialog) => {
      const r = dialog.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    };
    for (const dialog of document.querySelectorAll('dialog[closedby="any"]')) {
      let pressedOutside = false;
      dialog.addEventListener("pointerdown", (e) => {
        pressedOutside = outside(e, dialog);
      });
      dialog.addEventListener("pointerup", (e) => {
        if (pressedOutside && outside(e, dialog)) dialog.close();
      });
    }
  }
});
