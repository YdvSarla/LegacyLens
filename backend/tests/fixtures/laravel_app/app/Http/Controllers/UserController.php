<?php

namespace App\Http\Controllers;

use App\Models\User;
use App\Services\BillingService;
use Illuminate\Http\Request;

/**
 * Manages users.
 */
class UserController extends Controller
{
    public function __construct(private BillingService $billing)
    {
    }

    public function index()
    {
        return $this->ok(User::all());
    }

    public function store(Request $request)
    {
        $user = new User();
        $user->name = $request->input('name');
        $user->save();
        $this->billing->charge($user, 100);
        return $this->ok($user);
    }
}
