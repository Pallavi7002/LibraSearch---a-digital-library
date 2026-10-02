# LibraSearch — Digital Library Search Optimizer

A hackathon-ready Flask + SQLite project demonstrating Design and Analysis of Algorithms (DAA) concepts in a practical digital-library application.

## DAA concepts implemented

- Linear Search — baseline O(N)
- Hash-based exact lookup — average O(1) lookup
- Inverted Index — token -> resource IDs
- Merge Sort — O(N log N)
- Heap-based Top-K — O(N log K)
- Levenshtein distance — typo correction
- Jaccard similarity — related-resource recommendations

## Product features

- Home/search page
- Signup/login/logout
- Digital library browsing
- Advanced filters
- Search history
- Resource details
- Wishlist
- Cart
- Demo checkout/payment status
- My Library
- Algorithm Lab with live benchmark measurements
- Responsive mobile UI
- Dark mode
- Admin resource management
- Index rebuild
- Demo data seeding

## Run on Windows

```powershell
cd LibraSearch_DAA
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000

If PowerShell blocks activation, run the project without activation:

```powershell
py -m pip install -r requirements.txt
py app.py
```

## Demo accounts

Admin:
- Email: admin@librasearch.local
- Password: Admin@123

Demo user:
- Email: demo@librasearch.local
- Password: Demo@123

Change these for any real deployment.

## Project structure

```text
LibraSearch_DAA/
├── app.py
├── requirements.txt
├── README.md
├── library.db             # generated automatically
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── login.html
│   ├── signup.html
│   ├── library.html
│   ├── resource.html
│   ├── cart.html
│   ├── wishlist.html
│   ├── my_library.html
│   ├── dashboard.html
│   ├── algorithm_lab.html
│   ├── admin.html
│   ├── admin_resource.html
│   └── 404.html
├── static/
│   ├── style.css
│   └── app.js
└── tests/
    └── test_algorithms.py
```

## Hackathon demo flow

1. Search `machine learning`.
2. Explain the inverted index.
3. Try `machne learning` and show typo correction.
4. Open a resource and show related resources using Jaccard similarity.
5. Add a resource to cart and complete the simulated checkout.
6. Open My Library.
7. Open Algorithm Lab.
8. Compare Linear Search, Hash Search and Inverted Index.
9. Explain Merge Sort and Heap Top-K.

Benchmark timings are measured in the current browser/server environment and are not universal hardware benchmarks.

## Visual Classics & Reading Streaks

The enhanced build includes a curated visual Classics shelf featuring books by William Shakespeare, Jane Austen, Charles Dickens, Leo Tolstoy, Mark Twain, and J. K. Rowling. Classic book covers are loaded from Open Library cover URLs. The dashboard also includes a daily reading record, reading minutes, session counts, and a consecutive-day streak tracker. Use **Log 15 min** on a book page to record a reading session.
