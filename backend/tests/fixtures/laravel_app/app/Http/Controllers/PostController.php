<?php

namespace App\Http\Controllers;

use App\Models\Post;

class PostController extends Controller
{
    public function index() { return $this->ok(Post::all()); }
    public function show($id) { return $this->ok(Post::find($id)); }
}
